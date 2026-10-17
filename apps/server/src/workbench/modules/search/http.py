"""Bounded HTTP IO. Public requests pin DNS results without changing TLS identity."""

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from workbench.contracts.errors import AppError

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 50_000
HEADERS = {"User-Agent": "AIWorkbench/0.1 (public document reader)", "Accept-Encoding": "identity"}


def normalize_url(value: str, *, public: bool) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
            raise ValueError
        if len(str(url)) > 4096 or "%" in url.host:
            raise ValueError
        if public and url.port not in {None, 80 if url.scheme == "http" else 443}:
            raise ValueError
        if public:
            try:
                address = ipaddress.ip_address(url.host)
            except ValueError:
                address = None
            if address is not None and not public_ip(address):
                raise ValueError
        return url
    except (ValueError, httpx.InvalidURL):
        raise AppError(
            "web_url_forbidden",
            "URL 必须是不含凭据的 HTTP(S) 地址；公开网页仅支持标准端口及公网地址。",
            422,
        ) from None


def public_ip(address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.sixtofour or address.teredo:
            return False
        if address.ipv4_mapped:
            address = address.ipv4_mapped
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


async def pinned_address(url: httpx.URL) -> str:
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(
            url.host,
            url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise AppError("web_dns_failed", "无法解析网页域名。", 502, "transient", True) from None
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or any(not public_ip(ipaddress.ip_address(value)) for value in addresses):
        raise AppError(
            "web_address_forbidden", "网页域名解析到了受保护或非公网地址。", 403, "permission"
        )
    # No subsequent hostname lookup occurs: httpcore connects to this numeric IP.
    return sorted(addresses, key=lambda value: ipaddress.ip_address(value).version)[0]


def client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        headers=HEADERS,
    )


async def bounded_body(response: httpx.Response) -> bytes:
    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
        raise AppError(
            "web_encoding_unsupported",
            "服务忽略了无压缩请求；为保证读取预算，拒绝此编码。",
            502,
            "dependency",
        )
    data = bytearray()
    async for chunk in response.aiter_raw():
        if len(data) + len(chunk) > MAX_BYTES:
            raise AppError("web_response_too_large", "响应超过 2 MiB 读取预算。", 413, "dependency")
        data.extend(chunk)
    return bytes(data)


def status_error(response: httpx.Response, *, search: bool) -> None:
    prefix = "search" if search else "web"
    status = response.status_code
    if 300 <= status < 400:
        raise AppError(
            f"{prefix}_redirect_refused",
            "搜索连接不允许重定向，请配置最终服务地址。",
            502,
            "dependency",
        )
    if status in {401, 403}:
        message = (
            "搜索服务拒绝访问，请检查权限并启用 JSON 格式。" if search else "目标网页拒绝访问。"
        )
        raise AppError(f"{prefix}_access_denied", message, 502, "dependency")
    if status == 429:
        raise AppError(
            f"{prefix}_rate_limited", "目标服务限流，请稍后重试。", 503, "transient", True
        )
    if status >= 400:
        raise AppError(
            f"{prefix}_http_error",
            f"目标服务返回 HTTP {status}。",
            502,
            "transient" if status >= 500 else "dependency",
            status >= 500,
        )


async def public_document(value: str, timeout: float) -> tuple[httpx.URL, str, bytes, int]:
    url = normalize_url(value, public=True)
    try:
        async with asyncio.timeout(timeout):
            for hop in range(6):
                address = await pinned_address(url)
                headers = {
                    "Host": url.netloc.decode("ascii"),
                    "Accept": "text/html, text/plain;q=0.9",
                }
                # Documented httpcore extension preserves original TLS SNI and certificate name.
                dial_url = url.copy_with(host=address)
                async with client(timeout) as http:
                    async with http.stream(
                        "GET", dial_url, headers=headers, extensions={"sni_hostname": url.host}
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if hop == 5 or not response.headers.get("location"):
                                raise AppError(
                                    "web_redirect_limit",
                                    "网页重定向次数过多或缺少目标。",
                                    502,
                                    "dependency",
                                )
                            next_url = normalize_url(
                                urljoin(str(url), response.headers["location"]), public=True
                            )
                            if url.scheme == "https" and next_url.scheme != "https":
                                raise AppError(
                                    "web_redirect_downgrade",
                                    "拒绝从 HTTPS 降级到 HTTP。",
                                    403,
                                    "permission",
                                )
                            url = next_url
                            continue
                        status_error(response, search=False)
                        content_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                            raise AppError(
                                "web_type_unsupported",
                                "此入口仅支持 HTML 或纯文本；请使用文件流程处理其他类型。",
                                415,
                            )
                        return (
                            url,
                            response.headers.get("content-type", content_type),
                            await bounded_body(response),
                            response.status_code,
                        )
    except (TimeoutError, httpx.TimeoutException):
        raise AppError("web_timeout", "网页读取超时。", 504, "transient", True) from None
    except httpx.HTTPError:
        raise AppError(
            "web_network_error",
            "网页连接失败（含 TLS 校验失败）；未切换地址或重试。",
            502,
            "transient",
            True,
        ) from None
    raise AppError("web_redirect_limit", "网页重定向次数过多。", 502, "dependency")


class TextExtractor(HTMLParser):
    """Small non-executing HTML reader; not a browser and never fetches subresources."""

    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.size = 0
        self.skip: list[str] = []
        self.in_title = False
        self.title = ""
        self.truncated = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip.append(tag)
        if tag in self.BREAK and not self.skip:
            self.add("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BREAK and not self.skip:
            self.add("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.skip:
            self.skip = self.skip[: self.skip.index(tag)]
        if tag in self.BREAK and not self.skip:
            self.add("\n")

    def add(self, value: str):
        remaining = MAX_TEXT - self.size
        self.parts.append(value[:remaining])
        self.size += min(remaining, len(value))
        self.truncated |= len(value) > remaining

    def handle_data(self, value):
        if self.in_title:
            self.title = (self.title + value)[:500]
        if not self.skip:
            self.add(value)

    def text(self):
        return "\n".join(
            " ".join(line.split()) for line in "".join(self.parts).splitlines() if line.strip()
        )


def plain(value: str, limit: int) -> str:
    parser = TextExtractor()
    parser.feed(value[: min(len(value), MAX_TEXT)])
    return parser.text()[:limit]
