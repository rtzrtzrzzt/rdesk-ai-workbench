"""One access policy for native direct HTTP/TLS startup and request checks.

from_values is configuration-only: it performs no filesystem/network/SSL IO.
validate_tls_files is the explicit, read-only startup certificate preflight.
Identity, owner creation and CSRF remain application responsibilities.
"""

from __future__ import annotations

import ipaddress
import os
import re
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.private import check_private

_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_DEV = ("http://127.0.0.1:5173", "http://localhost:5173", "http://[::1]:5173")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class AccessPolicyError(ValueError):
    """Safe configuration failure; no certificate/key contents, paths or input echo."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code="access_address_invalid", message="访问地址或来源不符合精确原生访问策略。"):
    return AccessPolicyError(code, message)


def _host(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 253
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        or any(c in value for c in "/\\@?#%*[]")
        or value.endswith(".")
    ):
        raise _fail()
    value = value.lower()
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        labels = value.split(".")
        if (
            not all(_DNS_LABEL.fullmatch(label) for label in labels)
            or labels[-1].isdigit()
            or re.fullmatch(r"0x[0-9a-f]+", labels[-1])
        ):
            raise _fail() from None  # No ambiguous shortened/octal/hex IP addresses.
        return value


def _loopback(host):
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(
            getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def _unspecified(host):
    try:
        address = ipaddress.ip_address(host)
        return address.is_unspecified or bool(
            getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_unspecified
        )
    except ValueError:
        return False


def _authority(value, scheme):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 300
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        or any(c in value for c in "/\\@?#%*")
    ):
        raise _fail()
    # IPv6 must be bracketed in an HTTP authority. Explicit empty/invalid ports
    # are rejected rather than silently treated as default by urllib.parse.
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise _fail()
        host, suffix = value[1:end], value[end + 1 :]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise _fail() from None
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            raise _fail()
    else:
        if value.count(":") > 1:
            raise _fail()
        host, separator, raw_port = value.partition(":")
        if separator and not raw_port.isdigit():
            raise _fail()
    parsed = urlsplit(scheme + "://" + value)
    port = parsed.port or (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535 or (parsed.port is not None and parsed.port == 0):
        raise _fail()
    return _host(host), port


def _origin(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
    ):
        raise _fail()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
    ):
        raise _fail()
    host, port = _authority(parsed.netloc, parsed.scheme)
    if _unspecified(host):
        raise _fail("access_public_bind_invalid", "通配监听地址只能用于bind，不能作为公开来源。")
    return parsed.scheme, host, port


def _url(scheme, host, port):
    host = "[" + host + "]" if ":" in host else host
    default = 443 if scheme == "https" else 80
    return scheme + "://" + host + (":" + str(port) if port != default else "")


def _path(value):
    if value is None:
        return None
    if not isinstance(value, (str, Path)) or not str(value) or "\0" in str(value):
        raise _fail("access_tls_paths_invalid", "TLS证书和密钥必须分别指定本地文件路径。")
    text = str(value)
    if text.startswith(("\\\\", "//")) or text.lower().startswith(("http:", "https:", "file:")):
        raise _fail("access_tls_paths_invalid", "TLS材料必须是本机文件，不接受URL、UNC或设备路径。")
    # Keep paths lexical: do not resolve symlinks, expand HOME or touch disk here.
    return Path(value)


@dataclass(frozen=True)
class AccessPolicy:
    host: str
    port: int
    lan_enabled: bool
    scheme: str
    public_origins: tuple[str, ...]
    trusted_hosts: tuple[str, ...]
    dev_origins: tuple[str, ...]
    secure_cookies: bool
    ssl_certfile: Path | None
    ssl_keyfile: Path | None

    @classmethod
    def from_values(
        cls,
        host="127.0.0.1",
        port=8765,
        lan_enabled=False,
        public_origins=(),
        secure_cookies=False,
        ssl_certfile=None,
        ssl_keyfile=None,
    ):
        """Pure parsing. LAN configuration fails explicitly; no implicit fallback."""
        if type(port) is not int or not 1 <= port <= 65535:
            raise _fail("access_port_invalid", "监听端口必须是1至65535的整数。")
        if type(lan_enabled) is not bool or type(secure_cookies) is not bool:
            raise _fail("access_flag_invalid", "LAN与Secure Cookie配置必须是明确布尔值。")
        if lan_enabled:
            raise _fail(
                "access_lan_disabled", "当前版本已停用局域网访问；请关闭LAN并使用回环监听。"
            )
        host = _host(host)
        if not _loopback(host):
            raise _fail("access_loopback_required", "本机工作台只允许回环监听地址。")
        # Bind a literal address even for localhost configuration, so listener
        # safety does not depend on local DNS/hosts resolution.
        if host == "localhost":
            host = "127.0.0.1"
        certificate, key = _path(ssl_certfile), _path(ssl_keyfile)
        if (certificate is None) != (key is None):
            raise _fail("access_tls_pair_required", "TLS证书和密钥必须同时配置。")
        scheme = "https" if certificate is not None else "http"
        if (scheme == "https") != secure_cookies:
            raise _fail(
                "access_secure_cookie_invalid",
                "原生HTTPS必须启用Secure Cookie；HTTP不得假设HTTPS会话。",
            )
        if not isinstance(public_origins, (tuple, list)):
            raise _fail(
                "access_origins_invalid", "公开来源必须是精确origin的列表，不能是通配或逗号字符串。"
            )
        origins = []
        for value in public_origins:
            try:
                item = _origin(value)
            except ValueError as error:
                if isinstance(error, AccessPolicyError):
                    raise
                raise _fail() from None
            if item[0] != scheme or item[2] != port:
                raise _fail(
                    "access_origin_listener_mismatch", "直连来源必须与实际TLS协议及监听端口相同。"
                )
            if not _loopback(item[1]):
                raise _fail("access_lan_not_enabled", "回环模式不能配置远端来源。")
            canonical = _url(*item)
            if canonical not in origins:
                origins.append(canonical)
        if not origins:
            hosts = tuple(dict.fromkeys((*_LOOPBACK, host)))
            origins = [_url(scheme, item, port) for item in hosts]
        trusted = tuple(dict.fromkeys(_origin(item)[1] for item in origins))
        dev = _DEV if scheme == "http" and not public_origins else ()
        return cls(
            host,
            port,
            lan_enabled,
            scheme,
            tuple(origins),
            trusted,
            dev,
            secure_cookies,
            certificate,
            key,
        )

    @property
    def cors_origins(self):
        return tuple(dict.fromkeys((*self.public_origins, *self.dev_origins)))

    @property
    def primary_origin(self):
        return self.public_origins[0]

    @property
    def uvicorn_kwargs(self):
        return {
            "host": self.host,
            "port": self.port,
            "proxy_headers": False,
            "ssl_certfile": str(self.ssl_certfile) if self.ssl_certfile else None,
            "ssl_keyfile": str(self.ssl_keyfile) if self.ssl_keyfile else None,
        }

    def allows_peer(self, peer: str) -> bool:
        """The actual socket address, never a forwarded header or DNS name."""
        try:
            address = ipaddress.ip_address(peer)
            return address.is_loopback or bool(
                getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback
            )
        except ValueError:
            return False

    def allows_host(self, host_header: str, request_scheme: str) -> bool:
        """Use the actual ASGI scheme with uvicorn proxy_headers=False."""
        try:
            if request_scheme != self.scheme:
                return False
            host, port = _authority(host_header, request_scheme)
            return host in self.trusted_hosts and port == self.port
        except ValueError:
            return False

    def allows_origin(self, origin: str, request_scheme: str, host_header: str) -> bool:
        """Exact same app origin, or the explicit default-loopback dev exception."""
        if not self.allows_host(host_header, request_scheme):
            return False
        try:
            parsed = _origin(origin)
            canonical = _url(*parsed)
            host, port = _authority(host_header, request_scheme)
            return canonical in self.dev_origins or (
                canonical in self.public_origins and parsed == (request_scheme, host, port)
            )
        except ValueError:
            return False

    def validate_tls_files(self) -> None:
        """Read-only startup check: regular/readable/non-symlink files + key pair.

        This does not establish certificate expiry/SAN/CA/browser trust. A
        separately prepared authorized certificate and real-device TLS/login
        verification remain deployment requirements. Never starts a listener.
        """
        if self.scheme != "https":
            return
        try:
            certificate = _readable_file(self.ssl_certfile, private=False)
            key = _readable_file(self.ssl_keyfile, private=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2

            def no_password_prompt():
                raise _fail(
                    "access_tls_password_unsupported",
                    "加密TLS密钥需要独立解锁设计；此入口不交互读取口令。",
                )

            context.load_cert_chain(
                str(self.ssl_certfile), str(self.ssl_keyfile), password=no_password_prompt
            )
            if certificate != _readable_file(
                self.ssl_certfile, private=False
            ) or key != _readable_file(self.ssl_keyfile, private=True):
                raise _fail("access_tls_file_changed", "TLS文件在检查期间变化；未允许启动。")
        except AccessPolicyError:
            raise
        except (OSError, ValueError, VaultError):
            raise _fail(
                "access_tls_files_invalid", "TLS文件不可读、不符合私有权限、无效或证书密钥不匹配。"
            ) from None


def _readable_file(path, *, private):
    if path is None:
        raise _fail("access_tls_pair_required", "TLS证书和密钥必须同时配置。")
    absolute = path.absolute()
    for part in [absolute, *absolute.parents]:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _fail("access_tls_symlink_denied", "TLS路径不能经过符号链接或重解析点。")
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise _fail("access_tls_regular_required", "TLS材料必须是可核对的常规本地文件。")
    if private:
        check_private(path)  # Unix current-user-only mode / existing native Windows ACL.
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or not info.st_ino
            or (initial.st_dev, initial.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise _fail("access_tls_regular_required", "TLS材料必须是可核对的常规本地文件。")
        if private and os.name != "nt" and stat.S_IMODE(info.st_mode) not in {0o600, 0o400}:
            raise _fail("access_tls_key_private", "TLS私钥需要当前用户独占的0600或只读0400权限。")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    finally:
        os.close(descriptor)
