"""Explicit adapters; no provider SDK retries or arbitrary remote executable code."""

import base64
import binascii
import json
from urllib.parse import quote

import httpx

from workbench.contracts.errors import AppError
from workbench.modules.search.http import normalize_url, pinned_address

MAX_RESPONSE = 128 * 1024**2
MAX_OUTPUT = 512 * 1024**2


def headers(key):
    return {"Authorization": "Bearer " + key} if key else {}


def client(timeout):
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        timeout=httpx.Timeout(timeout, connect=10),
        headers={"Accept-Encoding": "identity"},
    )


async def read_body(response, limit):
    if response.headers.get("content-encoding", "identity") not in {"identity", ""}:
        raise AppError("media_response_encoding", "生成服务返回了未支持的压缩响应。", 502)
    data = bytearray()
    async for chunk in response.aiter_raw():
        if len(data) + len(chunk) > limit:
            raise AppError("media_response_size", "生成结果超过传输容量，请在提供方下载原件。", 413)
        data.extend(chunk)
    return bytes(data)


async def request(snapshot, key, method, path, *, payload=None, files=None, request_id=None):
    endpoint = snapshot["endpoint"].rstrip("/") + path
    auth = headers(key)
    if request_id:
        auth["Idempotency-Key"] = request_id
    try:
        async with client(snapshot["configuration"]["timeout_seconds"]) as http:
            kwargs = {"json": payload} if files is None else {"data": payload, "files": files}
            async with http.stream(method, endpoint, headers=auth, **kwargs) as response:
                raw = await read_body(response, MAX_RESPONSE)
                status = response.status_code
        if status >= 300:
            # Do not echo provider response bodies: those can contain credentials,
            # input payloads or signed URLs. A POST failure is never auto-replayed.
            raise AppError(
                "media_provider_http", f"生成服务返回HTTP {status}，请核对提供方任务记录。", 502
            )
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AppError(
                "media_provider_response", "生成服务返回了无法解析的结果，请核对提供方记录。", 502
            ) from None
        if not isinstance(result, dict):
            raise AppError("media_provider_response", "生成服务结果须为JSON对象。", 502)
        return result
    except (httpx.HTTPError, TimeoutError):
        raise AppError(
            "media_transport_unknown",
            "生成连接中断，提交结果待核对；不会自动重复提交。",
            409,
            "outcome_unknown",
        ) from None


async def submit(snapshot, key, values, references, request_id):
    if snapshot["driver"] == "media_http":
        payload = {
            "protocol": "workbench-media/1",
            "request_id": request_id,
            "model": snapshot["model_identifier"],
            "capability": snapshot["capability"],
            "input": {
                key: value
                for key, value in values.items()
                if key not in {"references", "connection_id"}
            },
            "references": [
                {
                    "filename": name,
                    "mime_type": mime,
                    "data_base64": base64.b64encode(data).decode(),
                }
                for name, mime, data in references
            ],
        }
        return await request(snapshot, key, "POST", "/jobs", payload=payload, request_id=request_id)
    payload = {
        "model": snapshot["model_identifier"],
        "prompt": values["prompt"],
        "n": 1,
        "size": f"{values['width']}x{values['height']}",
    }
    if not snapshot["model_identifier"].lower().startswith(("gpt-image", "chatgpt-image")):
        payload["response_format"] = "b64_json"
    if references:
        files = [("image[]", (name, data, mime)) for name, mime, data in references]
        return await request(
            snapshot,
            key,
            "POST",
            "/images/edits",
            payload=payload,
            files=files,
            request_id=request_id,
        )
    return await request(
        snapshot, key, "POST", "/images/generations", payload=payload, request_id=request_id
    )


def job_identifier(result):
    identifier = result.get("id") or result.get("job_id")
    if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
        raise AppError(
            "media_job_id_missing",
            "服务未返回可核对的任务标识，请检查提供方记录。",
            409,
            "outcome_unknown",
        )
    return identifier


async def poll(snapshot, key, identifier):
    return await request(snapshot, key, "GET", "/jobs/" + quote(identifier, safe=""))


async def stop(snapshot, key, identifier):
    return await request(
        snapshot, key, "POST", "/jobs/" + quote(identifier, safe="") + "/cancel", payload={}
    )


async def download(snapshot, key, url):
    target = httpx.URL(url)
    origin = httpx.URL(snapshot["endpoint"])
    same_origin = (target.scheme, target.host, target.port) == (
        origin.scheme,
        origin.host,
        origin.port,
    )
    auth, extensions = {}, {}
    if same_origin:
        normalize_url(str(target), public=False)
        auth = headers(key)
    else:
        # Provider-returned URLs are untrusted. Public-only pinned DNS, no
        # redirects and no API key forwarding to an output CDN.
        target = normalize_url(str(target), public=True)
        address = await pinned_address(target)
        auth["Host"] = target.netloc.decode("ascii")
        extensions["sni_hostname"] = target.host
        target = target.copy_with(host=address)
    async with client(120) as http:
        async with http.stream("GET", target, headers=auth, extensions=extensions) as response:
            if response.status_code != 200:
                raise AppError(
                    "media_download_failed", "产物下载失败；已保留提供方任务，不会重新生成。", 502
                )
            return await read_body(response, MAX_OUTPUT)


async def outputs(snapshot, key, response):
    if snapshot["driver"] == "openai_images":
        items = response.get("data", [])
        values = [
            {
                "filename": f"image-{index + 1}.png",
                "mime_type": "image/png",
                "data_base64": item.get("b64_json"),
                "url": item.get("url"),
            }
            for index, item in enumerate(items)
            if isinstance(item, dict)
        ]
    else:
        values = response.get("outputs", [])
    if not isinstance(values, list) or not 0 < len(values) <= 8:
        raise AppError(
            "media_outputs_missing", "服务完成但没有可下载的产物，请核对提供方记录。", 502
        )
    result, total = [], 0
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise AppError("media_output_invalid", "生成服务产物描述无效。", 502)
        if value.get("data_base64"):
            try:
                data = base64.b64decode(value["data_base64"], validate=True)
            except (ValueError, binascii.Error):
                raise AppError(
                    "media_output_invalid", "生成服务返回了损坏的文件编码。", 502
                ) from None
        elif value.get("url"):
            data = await download(snapshot, key, value["url"])
        else:
            raise AppError("media_output_invalid", "生成产物缺少文件内容或下载地址。", 502)
        total += len(data)
        if not data or total > MAX_OUTPUT:
            raise AppError("media_output_budget", "生成文件为空或超过512MiB容量。", 413)
        name = value.get("filename") or f"output-{index + 1}.bin"
        # Names are display metadata only. Storage uses our UUIDs.
        name = name.replace("\\", "/").split("/")[-1][:200] or "output.bin"
        result.append((name, value.get("mime_type", "application/octet-stream"), data))
    return result
