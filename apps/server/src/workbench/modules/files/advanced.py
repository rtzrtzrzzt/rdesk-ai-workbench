"""Bounded native OCR, Office/PDF conversion and selected video frames.

Worker-only: dispatch never authorizes paths or publishes artifacts. The existing
FileRun worker supplies authorized immutable input/version and an empty managed
attempt staging, enforces its disposable-process resource/cancellation boundary,
and publishes the returned files with its existing journal. Native programs are
not an OS sandbox. See runtime/file-processing/README.md for exact plan/results.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sysconfig
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.modules.files.processors import check_archive, detect_type
from workbench.modules.files.storage import file_digest, storage_path

PREFIX = PROJECT_ROOT / ".tools/file-components/root"
MAX_INPUT = 256 * 1024**2
MAX_FILE = 16 * 1024**2
MAX_TOTAL = 64 * 1024**2
MAX_TEXT = 1_000_000
LANGUAGES = ("eng", "chi_sim", "chi_tra")


def _error(code, message, kind="validation"):
    return AppError(code, message, 422, kind)


def _binary(name):
    private = PREFIX / (
        "usr/lib/libreoffice/program/soffice" if name == "soffice" else "usr/bin/" + name
    )
    if private.is_file():
        return str(private)
    return shutil.which(name) or (shutil.which("libreoffice") if name == "soffice" else None)


def _environment(work: Path | None = None):
    # An allowlist, not os.environ.copy(): no provider/DB/cookie/display secrets.
    env = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OMP_THREAD_LIMIT": "2",
        "SAL_USE_VCLPLUGIN": "svp",
    }
    triple = sysconfig.get_config_var("MULTIARCH")
    if triple:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            map(
                str,
                [
                    PREFIX / "usr/lib/libreoffice/program",
                    PREFIX / "usr/lib" / triple,
                    PREFIX / "lib" / triple,
                ],
            )
        )
    if work:
        for name in ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            env[name] = str(work)
    else:
        # Light probes must not initialize a real user's profile.
        env["HOME"] = os.devnull
    return env


def _stop(process):
    # All descendants inherit the worker's process group so its cancellation still
    # reaches them. Local timeout additionally stops the captured owned tree.
    import psutil

    children = []
    try:
        for child in psutil.Process(process.pid).children(recursive=True):
            children.append((child, child.create_time()))
    except psutil.Error:
        pass
    for child, created in reversed(children):
        try:
            if child.create_time() == created and child.is_running():
                child.kill()
        except psutil.Error:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _run(args, work, *, timeout=60, budget=MAX_FILE, stdout_limit=2 * 1024**2):
    """No shell, stdin, network credentials or unbounded pipe buffers."""
    with tempfile.TemporaryFile(dir=work) as stdout, tempfile.TemporaryFile(dir=work) as stderr:
        process = subprocess.Popen(
            [str(v) for v in args],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=_environment(work),
            cwd=work,
        )
        deadline = time.monotonic() + timeout
        previous = None

        def terminate(signum, frame):
            _stop(process)
            raise SystemExit(128 + signum)

        if hasattr(signal, "SIGTERM"):
            try:
                previous = signal.signal(signal.SIGTERM, terminate)
            except ValueError:  # Caller should use the existing disposable process.
                pass
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise _error(
                        "file_component_timeout",
                        "本地组件达到超时；原件保留，请缩小范围。",
                        "resource",
                    )
                if (
                    os.fstat(stdout.fileno()).st_size > stdout_limit
                    or os.fstat(stderr.fileno()).st_size > 2 * 1024**2
                ):
                    raise _error(
                        "file_component_output_budget", "本地组件输出超过预算，已停止。", "resource"
                    )
                sizes = []
                # Office atomically renames/unlinks its own temporary files.
                # A vanished entry is normal, not a parser failure.
                for path in work.parent.rglob("*"):
                    try:
                        if path.is_file() and not path.is_symlink():
                            sizes.append(path.stat().st_size)
                            if len(sizes) > 4096:
                                raise _error(
                                    "file_component_disk_budget",
                                    "组件临时文件数量超过预算。",
                                    "resource",
                                )
                    except FileNotFoundError:
                        continue
                if sum(sizes) > budget or any(size > MAX_FILE for size in sizes):
                    raise _error(
                        "file_component_disk_budget",
                        "本地组件临时输出超过预算，已停止。",
                        "resource",
                    )
                time.sleep(0.05)
            if process.returncode:
                # Never echo raw parser stderr or the user's content/paths.
                raise _error("file_component_failed", "本地组件处理失败；原件已保留。")
            stdout.seek(0)
            stderr.seek(0)
            result = stdout.read(stdout_limit + 1), stderr.read(2 * 1024**2 + 1)
            if len(result[0]) > stdout_limit or len(result[1]) > 2 * 1024**2:
                raise _error("file_component_output_budget", "本地组件输出超过预算。", "resource")
            return result
        finally:
            if process.poll() is None:
                _stop(process)
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)


def _probe(name, arguments):
    binary = _binary(name)
    if not binary:
        return None, None
    try:
        p = subprocess.run([binary, *arguments], capture_output=True, timeout=5, env=_environment())
        if p.returncode:
            return binary, None
        line = (p.stdout + p.stderr).decode("utf-8", errors="replace").splitlines()
        return binary, line[0][:180] if line else None
    except (OSError, subprocess.TimeoutExpired):
        return binary, None


def _tessdata():
    private = PREFIX / "usr/share/tesseract-ocr/5/tessdata"
    if private.is_dir():
        return private
    return None


def _installed_languages(binary):
    if not binary:
        return []
    args = [binary]
    if _tessdata():
        args += ["--tessdata-dir", str(_tessdata())]
    try:
        p = subprocess.run(
            [*args, "--list-langs"], capture_output=True, timeout=5, env=_environment()
        )
        return (
            [x for x in LANGUAGES if x in p.stdout.decode(errors="replace").splitlines()]
            if p.returncode == 0
            else []
        )
    except (OSError, subprocess.TimeoutExpired):
        return []


def capabilities() -> list[dict]:
    """Light local executable/version checks only; never OCR/model inference."""
    tess, tv = _probe("tesseract", ["--version"])
    pdf, pv = _probe("pdftoppm", ["-v"])
    office, ov = _probe("soffice", ["--version"])
    ffmpeg, fv = _probe("ffmpeg", ["-version"])
    ffprobe, fpv = _probe("ffprobe", ["-version"])
    languages = _installed_languages(tess)
    result = []
    for lang, title in [("eng", "英文"), ("chi_sim", "简体中文"), ("chi_tra", "繁体中文")]:
        result.append(
            {
                "id": "ocr_" + lang,
                "name": "离线 OCR / " + title,
                "formats": ["image"] + (["pdf"] if pdf and pv else []),
                "operations": ["ocr"] if tv and lang in languages else [],
                "status": "available" if tv and lang in languages else "not_installed",
                "version": tv,
                "limitation": "实际语言包；OCR 识别结果需与原件核对，不等于版面或印章理解。",
            }
        )
    result += [
        {
            "id": "office_conversion",
            "name": "受限 Office / PDF 转换",
            "formats": ["docx", "xlsx", "pptx", "odt", "ods", "odp"],
            "operations": ["convert_pdf"] if office and ov else [],
            "status": "available" if office and ov else "not_installed",
            "version": ov,
            "limitation": (
                "拒绝宏/嵌入对象/外部关系；私有 profile；原生进程无 OS 沙箱与网络隔离保证。"
            ),
        },
        {
            "id": "video_frames",
            "name": "显式时间范围视频帧",
            "formats": ["media"],
            "operations": ["video_frames"] if ffmpeg and fv and ffprobe and fpv else [],
            "status": "available" if ffmpeg and fv and ffprobe and fpv else "not_installed",
            "version": fv,
            "limitation": "最多12帧/120秒；音轨与字幕仅元数据，不代表完整音视频理解。",
        },
    ]
    from workbench.modules.files.asr import capability

    result.append(capability())
    return result


def _managed(path):
    root = get_settings().data_dir.resolve()
    try:
        relative = Path(path).absolute().relative_to(root)
    except (ValueError, TypeError):
        raise _error("advanced_path_invalid", "计划必须引用受管文件区。", "policy") from None
    return storage_path(relative.as_posix())


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error("advanced_plan_invalid", "文件处理参数超出受限范围。")
    return value


def _number(value, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise _error("advanced_plan_invalid", "时间范围超出受限范围。")
    return float(value)


def _require(name):
    binary = _binary(name)
    if not binary:
        raise _error("file_component_missing", "所需原生组件尚未安装或未验证。", "capability")
    return binary


def _result(plan, mime, kind):
    references = plan.get(
        "source_refs", [{"artifact_id": plan["artifact_id"], "version": plan["expected_hash"]}]
    )
    if not isinstance(references, list) or not 1 <= len(references) <= 8:
        raise _error("advanced_references_invalid", "必须保留已授权原件的来源版本。")
    for reference in references:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"artifact_id", "version"}
            or not isinstance(reference["artifact_id"], str)
            or not isinstance(reference["version"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", reference["version"])
        ):
            raise _error("advanced_references_invalid", "原件来源版本无效。")
    primary = next((r for r in references if r["artifact_id"] == plan["artifact_id"]), None)
    if primary is None:
        raise _error("advanced_references_invalid", "来源必须包含真实父原件。")
    return {
        "segments": [],
        "metadata": {
            "format": kind,
            "mime_type": mime,
            "processor_revision": "native-advanced:1",
            "source_artifact_id": plan["artifact_id"],
            "source_version": primary["version"],
            "input_version": plan["expected_hash"],
            "source_refs": references,
            "isolation": "bounded_native_process_not_os_sandbox",
        },
        "warnings": [],
        "complete": False,
        "exports": [],
    }


def _publish_candidate(source, target):
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_FILE:
        raise _error("file_component_output_invalid", "组件输出不是预算内常规文件。")
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    os.chmod(target, 0o600)


def _ocr(plan, source, output, work, result, kind):
    from PIL import Image, ImageOps
    from pypdf import PdfReader

    config = plan.get("ocr")
    if not isinstance(config, dict) or set(config) - {"languages", "pages", "dpi"}:
        raise _error("ocr_plan_invalid", "OCR 必须指定受限语言/页范围。")
    languages = config.get("languages")
    if (
        not isinstance(languages, list)
        or not 1 <= len(languages) <= 3
        or any(x not in LANGUAGES for x in languages)
        or len(set(languages)) != len(languages)
    ):
        raise _error("ocr_language_invalid", "OCR 语言必须显式选择英文、简体中文或繁体中文。")
    binary = _require("tesseract")
    result["metadata"]["component_versions"] = {"tesseract": _probe("tesseract", ["--version"])[1]}
    if not set(languages) <= set(_installed_languages(binary)):
        raise _error("ocr_language_missing", "所选 OCR 语言包尚未安装。", "capability")
    dpi = _integer(config.get("dpi", 150), 150, 200)
    images = []
    if kind == "pdf":
        pdf = PdfReader(source)
        if pdf.is_encrypted:
            raise _error("document_password_required", "PDF 已加密，请通过专用通道处理。")
        pages = config.get("pages")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 8 or len(set(pages)) != len(pages):
            raise _error("ocr_pages_required", "PDF OCR 必须显式选择1至8个不重复页码。")
        for page in pages:
            _integer(page, 1, len(pdf.pages))
            box = pdf.pages[page - 1].mediabox
            width, height = abs(float(box.width)), abs(float(box.height))
            if not math.isfinite(width * height) or width * height * (dpi / 72) ** 2 > 16_000_000:
                raise _error("ocr_pixel_budget", "所选 PDF 页超过1600万像素渲染预算。", "resource")
            prefix = work / f"page-{page}"
            _run(
                [
                    _require("pdftoppm"),
                    "-f",
                    page,
                    "-l",
                    page,
                    "-r",
                    dpi,
                    "-singlefile",
                    "-png",
                    source,
                    prefix,
                ],
                work,
                timeout=20,
                budget=MAX_TOTAL,
            )
            images.append((prefix.with_suffix(".png"), {"page": page, "dpi": dpi}))
        result["complete"] = len(pages) == len(pdf.pages)
        result["metadata"].update(total_pages=len(pdf.pages), selected_pages=pages)
    elif kind == "image":
        if config.get("pages"):
            raise _error("ocr_pages_invalid", "图像 OCR 不接受 PDF 页码。")
        with Image.open(source) as image:
            if image.width * image.height > 16_000_000 or getattr(image, "n_frames", 1) != 1:
                raise _error("ocr_image_budget", "OCR 仅支持1600万像素内单帧图像。", "resource")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.save(work / "image.png")
        images.append((work / "image.png", {"image": 1}))
        result["complete"] = True
    else:
        raise _error("ocr_format_unsupported", "OCR 仅支持单帧图像或选定 PDF 页。", "capability")
    consumed = 0
    for image, locator in images:
        args = [
            binary,
            image,
            "stdout",
            "-l",
            "+".join(languages),
            "--psm",
            "3",
            "-c",
            "tessedit_create_tsv=1",
        ]
        if _tessdata():
            args += ["--tessdata-dir", _tessdata()]
        raw, _ = _run(args, work, timeout=35, budget=MAX_TOTAL, stdout_limit=8 * 1024**2)
        rows = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t")
        for row in rows:
            if row.get("level") != "5" or not row.get("text", "").strip():
                continue
            text = row["text"]
            if consumed + len(text) > MAX_TEXT or len(result["segments"]) >= 20000:
                result["complete"] = False
                result["warnings"].append("OCR 文本/定位数量达到预算，仅返回已确认部分。")
                break
            consumed += len(text)
            result["segments"].append(
                {
                    "text": text,
                    "locator": {
                        **locator,
                        "bbox": {
                            "x": int(row["left"]),
                            "y": int(row["top"]),
                            "width": int(row["width"]),
                            "height": int(row["height"]),
                            "unit": "pixel",
                        },
                        "block": int(row["block_num"]),
                        "paragraph": int(row["par_num"]),
                        "line": int(row["line_num"]),
                        "word": int(row["word_num"]),
                        "confidence": float(row["conf"]),
                    },
                }
            )
    result["metadata"]["ocr_languages"] = languages
    result["warnings"].append(
        "离线 OCR 为实际识别文本；词框是渲染像素坐标，置信分不是准确率，"
        "未识别版面、印章、签名或公式语义。"
    )
    if not result["segments"]:
        result["warnings"].append("所选范围未识别到文字；不意味着原件无内容。")


def _safe_office(source, filename):
    """CLI conversion has no per-load UNO MacroExecutionMode: reject active inputs."""
    from defusedxml import ElementTree

    suffix = Path(filename).suffix.lower()
    if suffix not in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}:
        raise _error(
            "office_format_unsupported",
            "仅允许受限无宏 OOXML/ODF 转 PDF；旧二进制 Office/RTF 尚未启用。",
            "capability",
        )
    check_archive(source)
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            name = entry.filename.lower()
            if any(
                part in name
                for part in (
                    "vbaproject",
                    "vbasignature",
                    "/embeddings/",
                    "/activex/",
                    "scripts/",
                    "basic/",
                    "objectreplacements/",
                    "oleobject",
                    "xl/connections.xml",
                    "xl/querytables/",
                    "xl/externallinks/",
                )
            ) or (
                name.endswith((".bin", ".exe", ".js"))
                and not re.fullmatch(r"(?:ppt|xl)/printersettings/printersettings[0-9]+\.bin", name)
            ):
                raise _error(
                    "office_active_content_denied",
                    "文档包含宏、活动或嵌入对象，受限转换拒绝执行。",
                    "policy",
                )
            if name.endswith((".xml", ".rels")):
                if entry.file_size > 16 * 1024**2:
                    raise _error("office_xml_budget", "Office XML 超过解析预算。", "resource")
                tree = ElementTree.fromstring(archive.read(entry))
                for element in tree.iter():
                    attrs = element.attrib
                    if attrs.get("{http://www.w3.org/XML/1998/namespace}base"):
                        raise _error(
                            "office_external_content_denied",
                            "文档含XML资源基址；拒绝执行。",
                            "policy",
                        )
                    target = unquote(attrs.get("Target", ""))
                    if ":" in target or target.startswith(("/", "\\")):
                        raise _error(
                            "office_external_content_denied",
                            "文档含绝对资源目标；拒绝执行。",
                            "policy",
                        )
                    local = element.tag.rsplit("}", 1)[-1]
                    if local in {
                        "instrText",
                        "fldSimple",
                        "fldChar",
                        "object",
                        "object-ole",
                        "plugin",
                        "applet",
                        "script",
                    }:
                        raise _error(
                            "office_active_content_denied",
                            "文档含动态域、脚本或嵌入对象；受限转换拒绝执行。",
                            "policy",
                        )
                    if suffix == ".xlsx" and local in {"f", "definedName"} and element.text:
                        from workbench.modules.files.exports_dto import FormulaCell

                        try:
                            FormulaCell.restricted_formula("=" + element.text.lstrip("="))
                        except ValueError:
                            raise _error(
                                "office_formula_denied",
                                "文档含未批准或外部公式；拒绝执行。",
                                "policy",
                            ) from None
                    if any(key.endswith("}formula") for key in attrs):
                        raise _error(
                            "office_formula_denied",
                            "ODF 公式尚未接入受限验证；拒绝执行。",
                            "policy",
                        )
                    if attrs.get("TargetMode", "").lower() == "external":
                        raise _error(
                            "office_external_content_denied",
                            "文档含外部关系；请先移除链接或外部资源。",
                            "policy",
                        )
                    for key, value in attrs.items():
                        value = unquote(value)
                        if key.endswith("}href") and (
                            ":" in value
                            or value.startswith(("/", "\\"))
                            or ".." in value.replace("\\", "/").split("/")
                        ):
                            raise _error(
                                "office_external_content_denied",
                                "文档含外部资源；受限转换拒绝读取。",
                                "policy",
                            )
    return suffix


def _pdf(plan, source, output, work, result):
    suffix = _safe_office(source, plan["filename"])
    binary = _require("soffice")
    result["metadata"]["component_versions"] = {"libreoffice": _probe("soffice", ["--version"])[1]}
    private_input = work / ("input" + suffix)
    # Exactly the immutable authorized original; no sibling project access implied.
    with source.open("rb") as src, private_input.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    profile = work / "profile"
    user = profile / "user"
    user.mkdir(parents=True, mode=0o700)
    (user / "registrymodifications.xcu").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">
<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
<prop oor:name="SecureURL" oor:op="fuse"><value/></prop></item>
<item oor:path="/org.openoffice.Office.Common/Save/Document">
<prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>""",
        encoding="utf-8",
    )
    converted = work / "converted"
    converted.mkdir(mode=0o700)
    _run(
        [
            binary,
            "-env:UserInstallation=" + profile.as_uri(),
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            converted,
            private_input,
        ],
        work,
        timeout=90,
        budget=MAX_TOTAL,
    )
    candidate = converted / "input.pdf"
    if not candidate.is_file() or candidate.is_symlink():
        raise _error("office_conversion_failed", "组件未生成可确认的新 PDF；原件保留。")
    with candidate.open("rb") as stream:
        signature = stream.read(5)
    if signature != b"%PDF-":
        raise _error("office_conversion_failed", "组件未生成可确认的新 PDF；原件保留。")
    from pypdf import PdfReader

    pages = len(PdfReader(candidate).pages)
    _publish_candidate(candidate, output / "export.pdf")
    result["exports"] = [
        {
            "filename": "export.pdf",
            "display_name": Path(plan["filename"]).stem + ".pdf",
            "mime_type": "application/pdf",
            "kind": "export",
            "source_refs": result["metadata"]["source_refs"],
        }
    ]
    result["metadata"].update(output_format="pdf", output_pages=pages)
    result["complete"] = True
    result["warnings"] += [
        "生成新的 PDF 副本；字体替换、排版和动态字段/公式显示可能变化，不承诺无损回环。",
        "已拒绝宏/嵌入对象/外部关系并使用私有高安全 profile；原生解析无 OS 沙箱或网络隔离保证。",
    ]


def _frames(plan, source, output, work, result):
    config = plan.get("video")
    if not isinstance(config, dict) or set(config) - {"start_seconds", "end_seconds", "max_frames"}:
        raise _error("video_plan_invalid", "帧提取必须指定起止时间及最大帧数。")
    start = _number(config.get("start_seconds"), 0, 86400)
    end = _number(config.get("end_seconds"), 0, 86400)
    count = _integer(config.get("max_frames"), 1, 12)
    if end <= start or end - start > 120:
        raise _error("video_range_invalid", "帧提取区间必须大于0且不超过120秒。")
    raw, _ = _run(
        [
            _require("ffprobe"),
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,matroska,webm,avi,mpeg,mpegts,ogg",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,duration,start_time:stream_tags=language",
            "-of",
            "json",
            source,
        ],
        work,
        timeout=15,
    )
    metadata = json.loads(raw)
    streams = metadata.get("streams", [])
    videos = [x for x in streams if x.get("codec_type") == "video"]
    if not videos:
        raise _error("video_stream_missing", "原件没有可提取的视频轨道。")
    if int(videos[0].get("width", 0)) * int(videos[0].get("height", 0)) > 16_000_000:
        raise _error("video_pixel_budget", "原视频超过1600万像素解码预算。", "resource")
    try:
        duration = float(metadata.get("format", {}).get("duration", "nan"))
    except ValueError:
        duration = float("nan")
    if math.isfinite(duration) and end > duration + 0.001:
        raise _error("video_range_invalid", "所选区间超出原视频实际时长。")
    result["metadata"]["component_versions"] = {
        "ffmpeg": _probe("ffmpeg", ["-version"])[1],
        "ffprobe": _probe("ffprobe", ["-version"])[1],
    }
    result["metadata"].update(
        video_stream=videos[0],
        audio_streams=[x for x in streams if x.get("codec_type") == "audio"],
        subtitle_streams=[x for x in streams if x.get("codec_type") == "subtitle"],
        selected_range={"start_seconds": start, "end_seconds": end},
        duration_seconds=duration if math.isfinite(duration) else None,
    )
    # copyts preserves source timestamps. showinfo is BEFORE scale:
    # the locator records the actual decoded frame PTS, not the requested sample.
    for index in range(count):
        requested = start + (end - start) * index / count
        candidate = work / f"frame-{index + 1:04d}.png"
        _, log = _run(
            [
                _require("ffmpeg"),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "info",
                "-threads",
                "2",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "mov,matroska,webm,avi,mpeg,mpegts,ogg",
                "-copyts",
                "-i",
                source,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"select=gte(t\\,{requested:.6f})*lt(t\\,{end:.6f}),"
                "showinfo,scale=1600:1600:force_original_aspect_ratio=decrease",
                "-frames:v",
                "1",
                "-fps_mode",
                "passthrough",
                "-threads",
                "2",
                candidate,
            ],
            work,
            timeout=20,
            budget=MAX_TOTAL,
        )
        times = [float(value) for value in re.findall(rb"pts_time:([0-9.eE+\-]+)", log)]
        if not candidate.exists() or not times:
            result["warnings"].append(f"第{index + 1}个采样位置未返回可定位帧。")
            continue
        timestamp = times[0]
        if not math.isfinite(timestamp) or timestamp < start - 0.001 or timestamp >= end + 0.001:
            raise _error(
                "video_timestamp_unconfirmed", "组件返回的帧时间戳不在授权区间；停止发布。"
            )
        filename = candidate.name
        _publish_candidate(candidate, output / filename)
        result["exports"].append(
            {
                "filename": filename,
                "display_name": Path(plan["filename"]).stem + "-" + filename,
                "mime_type": "image/png",
                "kind": f"frame_{index + 1:04d}",
                "timestamp_seconds": timestamp,
                "requested_timestamp_seconds": requested,
                "source_refs": result["metadata"]["source_refs"],
            }
        )
    result["complete"] = len(result["exports"]) == count
    result["warnings"].append(
        "仅为显式范围内的有限视频帧；音轨/字幕仅真实元数据，未进行 ASR 或完整音视频理解。"
    )


def dispatch(plan: dict, staging: Path | None = None) -> dict:
    """Synchronous disposable-processor adapter; no DB, network fetch or publication."""
    required = {
        "input",
        "output",
        "filename",
        "artifact_id",
        "expected_hash",
        "expected_bytes",
        "operation",
    }
    if not isinstance(plan, dict) or not required <= plan.keys():
        raise _error("advanced_plan_invalid", "缺少已授权文件处理计划。")
    source, output = (
        _managed(plan["input"]),
        _managed(staging if staging is not None else plan["output"]),
    )
    if (
        output != _managed(plan["output"])
        or not output.is_dir()
        or source.is_relative_to(output)
        or output.is_relative_to(source)
    ):
        raise _error("advanced_path_invalid", "处理输入与独立 staging 必须分离。", "policy")
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_INPUT:
        raise _error("advanced_input_budget", "原件不是预算内受管常规文件。", "resource")
    if (
        source.stat().st_size != plan["expected_bytes"]
        or file_digest(source) != plan["expected_hash"]
    ):
        raise AppError("artifact_version_changed", "原件版本已变化，停止处理。", 409, "conflict")
    mime, kind = detect_type(source, plan["filename"], deep=True)
    result = _result(plan, mime, kind)
    with tempfile.TemporaryDirectory(prefix=".advanced-", dir=output) as temporary:
        work = Path(temporary)
        os.chmod(work, 0o700)
        if plan["operation"] == "ocr":
            _ocr(plan, source, output, work, result, kind)
        elif plan["operation"] == "convert_pdf":
            _pdf(plan, source, output, work, result)
        elif plan["operation"] == "video_frames" and kind == "media":
            _frames(plan, source, output, work, result)
        elif plan["operation"] == "transcribe":
            from workbench.modules.files.asr import transcribe

            transcribe(plan, source, output, work, result, run=_run, require=_require)
        else:
            raise _error(
                "advanced_operation_unsupported", "此原件不支持所选受限处理操作。", "capability"
            )
        if (
            source.stat().st_size != plan["expected_bytes"]
            or file_digest(source) != plan["expected_hash"]
        ):
            raise AppError(
                "artifact_version_changed", "处理期间原件发生变化，候选不得发布。", 409, "conflict"
            )
    return result
