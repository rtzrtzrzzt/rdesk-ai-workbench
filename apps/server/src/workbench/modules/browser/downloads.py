"""Original HTTP bytes through the owned session proxy; no browser-cookie access."""

import asyncio
import hashlib
import os
import re
import stat
import time
import unicodedata
from email.message import Message
from pathlib import Path
from urllib.parse import unquote

import httpx

from workbench.contracts.errors import AppError
from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.private import private_directory, read_private

MAX_BYTES = 16 * 1024 * 1024
TIMEOUT = 30


def failure(code, message, status=409, kind="conflict"):
    return AppError(code, message, status, kind, False)


def display_filename(disposition: str, url: httpx.URL) -> str:
    message = Message()
    message["content-disposition"] = disposition[:8192]
    suggested = message.get_filename() or unquote(url.path.rsplit("/", 1)[-1]) or "download.bin"
    value = unicodedata.normalize("NFKC", str(suggested))
    value = "".join(
        "_" if c in '/\\:*?"<>|' or unicodedata.category(c).startswith("C") else c for c in value
    ).strip(" .")[:180]
    if not value or value.split(".", 1)[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *{f"COM{i}" for i in range(1, 10)},
        *{f"LPT{i}" for i in range(1, 10)},
    }:
        return "download.bin"
    return value


def _headers(response):
    encodings = response.headers.get_list("content-encoding")
    if len(encodings) > 1 or (encodings and encodings[0].strip().lower() not in {"", "identity"}):
        raise failure(
            "browser_download_encoding", "目标返回压缩编码；未解压或保存为原件。", 502, "dependency"
        )
    lengths = response.headers.get_list("content-length")
    if lengths:
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0]):
            raise failure("browser_download_length", "目标长度声明无效。", 502, "dependency")
        if int(lengths[0]) > MAX_BYTES:
            raise failure("browser_download_budget", "下载超过 16 MiB 上限。", 413, "budget")
    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if len(mime) <= 127 and re.fullmatch(r"[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+", mime):
        return mime, "response_header"
    return "application/octet-stream", "fallback"


def verify_candidate(result) -> None:
    """Do not re-fetch a cached request whose host already moved/lost its file."""
    try:
        data = read_private(Path(result.candidate_path), MAX_BYTES)
        if len(data) != result.bytes or hashlib.sha256(data).hexdigest() != result.sha256:
            raise ValueError
    except (OSError, ValueError, VaultError):
        raise failure(
            "browser_download_candidate_unavailable",
            "本次下载曾完成，但候选已被发布、移走或改变；请查持久操作回执，不会重复下载。",
            409,
            "outcome_unknown",
        ) from None


def _unlink_owned(path, identity):
    try:
        info = path.lstat()
        if (info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode):
            raise OSError("owned candidate identity changed")
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        raise failure(
            "browser_download_cleanup_unknown",
            "未确认候选清理；现有未知归属路径未删除，请核对本次 staging。",
            409,
            "outcome_unknown",
        ) from None


async def fetch(policy, proxy_address, source_url, output, request_id, check_observation):
    """Return only fully received/fsynced/hash-verified original bytes metadata.

    The caller already validated owner/staging/ref and holds the session writer
    lock. Every request/redirect and final result rechecks that observation.
    """
    if not proxy_address:
        raise failure("browser_download_proxy_closed", "会话代理已关闭。", 410, "resource")
    endpoint = httpx.URL(proxy_address)
    if endpoint.scheme != "http" or endpoint.host != "127.0.0.1" or not endpoint.port:
        raise failure("browser_download_proxy_invalid", "会话代理身份无效。", 503, "dependency")
    try:
        private_directory(output)
    except (OSError, VaultError):
        raise failure(
            "browser_download_output_private",
            "下载目录必须是已存在的私有 staging。",
            403,
            "permission",
        ) from None
    target = output / f"browser-download-{request_id}.candidate"
    try:
        descriptor = os.open(
            target,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        raise failure(
            "browser_download_output_conflict",
            "本次请求已有候选或残留；请由持久操作核对，未覆盖或重复下载。",
            409,
            "outcome_unknown",
        ) from None
    identity = os.fstat(descriptor)
    identity = (identity.st_dev, identity.st_ino)
    complete = False
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            async with asyncio.timeout(TIMEOUT):
                transport = httpx.AsyncHTTPTransport(
                    proxy=proxy_address,
                    retries=0,
                    trust_env=False,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(TIMEOUT, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                    headers={
                        "User-Agent": "AIWorkbench/0.1 (explicit managed download)",
                        "Accept": "*/*",
                        "Accept-Encoding": "identity",
                    },
                ) as client:
                    url = policy.validate(source_url).copy_with(fragment=None)
                    for hop in range(4):
                        await check_observation()
                        policy.validate(str(url))
                        client.cookies.clear()  # Never adopt redirect Set-Cookie either.
                        async with client.stream("GET", url) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                if hop == 3:
                                    raise failure(
                                        "browser_download_redirect_limit",
                                        "下载重定向超过 3 跳上限。",
                                        502,
                                        "dependency",
                                    )
                                locations = response.headers.get_list("location")
                                if len(locations) != 1 or len(locations[0]) > 4096:
                                    raise failure(
                                        "browser_download_redirect_invalid",
                                        "下载重定向目标缺失或无效。",
                                        502,
                                        "dependency",
                                    )
                                url = policy.validate(str(url.join(locations[0]))).copy_with(
                                    fragment=None
                                )
                                continue
                            if response.status_code in {401, 403}:
                                raise failure(
                                    "browser_download_access_denied",
                                    "目标拒绝静态下载；此入口不继承网页登录 Cookie 或凭据。",
                                    403,
                                    "permission",
                                )
                            if response.status_code != 200:
                                raise failure(
                                    "browser_download_http_status",
                                    "目标没有返回完整下载正文；未保存为成功结果。",
                                    502,
                                    "dependency",
                                )
                            mime, mime_source = _headers(response)
                            filename = display_filename(
                                response.headers.get("content-disposition", ""), url
                            )
                            total, hashed, checked = 0, hashlib.sha256(), time.monotonic()
                            async for chunk in response.aiter_raw(chunk_size=65536):
                                if total + len(chunk) > MAX_BYTES:
                                    raise failure(
                                        "browser_download_budget",
                                        "下载超过 16 MiB 上限。",
                                        413,
                                        "budget",
                                    )
                                stream.write(chunk)
                                total += len(chunk)
                                hashed.update(chunk)
                                if time.monotonic() - checked >= 0.2:
                                    await check_observation()
                                    checked = time.monotonic()
                            await check_observation()
                            stream.flush()
                            os.fsync(stream.fileno())
                            stream.seek(0)
                            actual = hashlib.file_digest(stream, "sha256").hexdigest()
                            info = target.lstat()
                            if (
                                (info.st_dev, info.st_ino) != identity
                                or info.st_size != total
                                or info.st_nlink != 1
                                or actual != hashed.hexdigest()
                            ):
                                raise failure(
                                    "browser_download_candidate_changed",
                                    "下载候选发生变化，未发布。",
                                )
                            result = {
                                "candidate_path": str(target),
                                "sha256": actual,
                                "bytes": total,
                                "mime": mime,
                                "mime_source": mime_source,
                                "filename": filename,
                                "source_url": source_url,
                                "final_url": str(url),
                            }
                            break
        # Both HTTP contexts and the owned descriptor have actually closed before
        # success. Cancellation while closing still removes this candidate.
        complete = True
        return result
    except TimeoutError:
        raise failure(
            "browser_download_timeout",
            "下载超过 30 秒，未保留完整候选；不会自动重试。",
            504,
            "transient",
        ) from None
    except httpx.HTTPError:
        raise failure(
            "browser_download_network",
            "下载传输未完整结束，候选已清理；不会自动重试。",
            502,
            "transient",
        ) from None
    finally:
        if not complete:
            _unlink_owned(target, identity)
