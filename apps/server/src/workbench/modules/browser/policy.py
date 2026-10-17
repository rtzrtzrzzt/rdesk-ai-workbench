"""Exact-origin policy, including a separately configured local acceptance exception."""

import asyncio
import ipaddress
import socket
from urllib.parse import unquote

import httpx

from workbench.contracts.errors import AppError
from workbench.modules.search.http import public_ip


def deny(message="浏览器目标不在本会话允许范围内。"):
    return AppError("browser_target_forbidden", message, 403, "permission")


def parsed(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or len(value) > 4096
            or "%" in url.host
        ):
            raise ValueError
        return url
    except (ValueError, httpx.InvalidURL):
        raise deny("浏览器只接受不含凭据的 HTTP(S) URL。") from None


def origin(value: str) -> str:
    url = parsed(value)
    return str(url.copy_with(path="/", query=None, fragment=None)).rstrip("/")


class NetworkPolicy:
    def __init__(self, public: list[str], local: list[str], authorized_local: set[str]):
        self.public = {self._exact(value) for value in public}
        self.local = {self._exact(value) for value in local}
        if not self.local.issubset(authorized_local):
            raise deny("本机会话目标没有得到 companion 管理员的精确 origin 授权。")
        if not self.public | self.local:
            raise deny("请先明确允许访问的网络 origin。")
        for value in self.public:
            url = parsed(value)
            if url.port not in {None, 80 if url.scheme == "http" else 443}:
                raise deny("公开网页只允许标准端口。")
            try:
                if not public_ip(ipaddress.ip_address(url.host)):
                    raise deny()
            except ValueError:
                if url.host == "localhost" or url.host.endswith(".localhost"):
                    raise deny() from None
        for value in self.local:
            if parsed(value).host not in {"127.0.0.1", "::1"}:
                raise deny("本机特例必须使用精确 loopback IP origin。")

    @staticmethod
    def _exact(value: str) -> str:
        url = parsed(value)
        if url.path not in {"", "/"} or url.query or url.fragment:
            raise deny("允许范围必须是精确 origin，不能包含路径、查询或通配符。")
        return origin(value)

    def validate(self, value: str) -> httpx.URL:
        url = parsed(value)
        source = origin(value)
        if source not in self.public | self.local:
            raise deny()
        if source in self.local:
            path = unquote(unquote(url.path)).replace("\\", "/")
            parts = []
            for item in path.split("/"):
                if item == "..":
                    if parts:
                        parts.pop()
                elif item not in {"", "."}:
                    parts.append(item)
            if parts and parts[0].lower() == "api":
                raise deny("受管浏览器不能访问工作台自身 API。")
        return url

    async def address(self, value: str) -> tuple[str, int]:
        url = self.validate(value)
        port = url.port or (443 if url.scheme == "https" else 80)
        if origin(value) in self.local:
            return url.host, port
        try:
            async with asyncio.timeout(5):
                answers = await asyncio.get_running_loop().getaddrinfo(
                    url.host, port, type=socket.SOCK_STREAM
                )
        except (socket.gaierror, TimeoutError):
            raise AppError(
                "browser_dns_failed", "浏览器目标 DNS 解析失败。", 502, "transient"
            ) from None
        addresses = {answer[4][0] for answer in answers}
        if not addresses or any(not public_ip(ipaddress.ip_address(ip)) for ip in addresses):
            raise deny("目标域名解析到了受保护网段；未放宽 DNS 或切换目标。")
        return sorted(addresses, key=lambda item: (ipaddress.ip_address(item).version, item))[
            0
        ], port
