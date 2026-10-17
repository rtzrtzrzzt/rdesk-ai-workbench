"""Optional offline whisper.cpp CPU adapter. Importing never probes or loads a model."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import wave
from pathlib import Path

from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError

REVISION = "d1be6fde11ac6e0407606b4e42fe72d34add8037"
VERSION = "1.9.5"
DEFAULT_ENGINE = (
    PROJECT_ROOT / ".tools/asr" / ("whisper.cpp-" + REVISION) / "build-cpu/bin/whisper-cli"
)
MAX_MODEL_BYTES = 512 * 1024**2
MAX_AUDIO_SECONDS = 120
MAX_OUTPUT_BYTES = 4 * 1024**2
LANGUAGES = frozenset(
    "auto en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta "
    "no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw "
    "gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg "
    "as tt haw ln ha ba jw su yue".split()
)
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac"})
AUDIO_FORMATS = "wav,mp3,flac,ogg,aac,mov"


def _error(code, message, kind="capability"):
    return AppError("asr_" + code, message, 422, kind)


def _identity(value, *, executable=False, model=False):
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path.absolute():
        raise _error("path_invalid", "ASR组件须使用明确的绝对常规文件路径，不接受符号链接。")
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_size <= 0:
        raise _error("path_invalid", "ASR组件或模型不是非空常规文件。")
    if executable and not os.access(path, os.X_OK):
        raise _error("engine_unavailable", "ASR原生引擎不可执行。")
    if model:
        if value.st_size > MAX_MODEL_BYTES:
            raise _error(
                "model_budget", "当前ASR子进程只允许不超过512 MiB的用户配置模型。", "resource"
            )
        with path.open("rb") as stream:
            # The pinned upstream loader checks this GGML magic; no weight hash/load.
            if stream.read(4) != b"lmgg":
                raise _error("model_format", "请配置whisper.cpp格式的本地GGML Whisper模型。")
    return {
        "path": str(path),
        "device": value.st_dev,
        "inode": value.st_ino,
        "bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _version(path):
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": os.devnull}
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [path, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            env=environment,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise _error("engine_unavailable", "ASR引擎版本检查超时。") from None
        output.seek(0)
        text = output.read(4097)
    if (
        process.returncode
        or len(text) > 4096
        or text.strip() != ("whisper.cpp version: " + VERSION).encode()
    ):
        raise _error("engine_version", "ASR引擎须为已核对的whisper.cpp 1.9.5。")
    return VERSION


def read_plan(*, environ=None):
    """Private server config, not a public DTO. Never infer/download a model path."""
    environment = os.environ if environ is None else environ
    engine = environment.get("WORKBENCH_ASR_ENGINE_PATH") or str(DEFAULT_ENGINE)
    model = environment.get("WORKBENCH_ASR_MODEL_PATH")
    result = {
        "state": "not_configured",
        "version": None,
        "reason": "尚未显式配置本地ASR模型；不会自动下载。",
    }
    try:
        if Path(engine).is_file():
            result["engine"] = _identity(engine, executable=True)
            result["version"] = _version(result["engine"]["path"])
        if not model:
            return result
        if "engine" not in result:
            return {**result, "state": "not_installed", "reason": "本地ASR引擎尚未准备。"}
        result["model"] = _identity(model, model=True)
        for name in ("ffmpeg", "ffprobe"):
            private = PROJECT_ROOT / ".tools/file-components/root/usr/bin" / name
            found = (
                str(private)
                if private.is_file()
                else shutil.which(
                    name, path=environment.get("PATH", os.environ.get("PATH", os.defpath))
                )
            )
            if not found:
                return {
                    **result,
                    "state": "not_installed",
                    "reason": "本地ASR需要已配置的FFmpeg与ffprobe。",
                }
            # Freeze the actual binary, not the parent's PATH or a symlink alias.
            result[name] = _identity(str(Path(found).resolve(strict=True)), executable=True)
        return {
            **result,
            "state": "available",
            "reason": "本地CPU引擎及显式模型文件预检通过；尚不代表转录效果已验收。",
        }
    except (OSError, ValueError):
        return {
            **result,
            "state": "not_configured",
            "reason": "ASR组件或模型路径不可用，请在本机重新配置。",
        }
    except AppError as error:
        return {
            **result,
            "state": "not_configured",
            "reason": error.message,
            "error_code": error.code,
        }


def capability():
    plan = read_plan()
    ready = plan["state"] == "available"
    return {
        "id": "asr",
        "name": "本地CPU语音转写",
        "formats": ["media"],
        "operations": ["transcribe"] if ready else [],
        "status": plan["state"],
        "version": plan["version"],
        "limitation": plan["reason"]
        + " 仅音频/120秒/2线程/原2 GiB子进程预算；不下载模型，不保证准确率。",
    }


def freeze_plan(asr_options, *, environ=None):
    """Server-only admission: request options contain no engine/model/output paths."""
    if type(asr_options) is not dict or asr_options.keys() - {"language", "timestamps"}:
        raise _error(
            "options", "ASR仅接受language及timestamps选项，不接受模型或文件路径。", "validation"
        )
    if asr_options.get("timestamps") is not True:
        raise _error("options", "当前ASR必须保留真实分段时间戳。", "validation")
    language = asr_options.get("language", "auto")
    if type(language) is not str or language not in LANGUAGES:
        raise _error("language", "ASR语言码不在固定引擎支持范围内。", "validation")
    configured = read_plan(environ=environ)
    if configured["state"] != "available":
        raise _error("not_configured", configured["reason"])
    return {
        "schema": "whisper-cpp-cpu:1",
        "engine_version": VERSION,
        "engine_revision": REVISION,
        "engine": configured["engine"],
        "model": configured["model"],
        "ffmpeg": configured["ffmpeg"],
        "ffprobe": configured["ffprobe"],
        "device": "cpu",
        "threads": 2,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
        "language": language,
        "timestamps": True,
    }


def _validate_frozen(runtime, options):
    if type(runtime) is not dict or set(runtime) != {
        "schema",
        "engine_version",
        "engine_revision",
        "engine",
        "model",
        "ffmpeg",
        "ffprobe",
        "device",
        "threads",
        "max_audio_seconds",
        "language",
        "timestamps",
    }:
        raise _error("plan", "缺少由服务端冻结的ASR运行计划。", "policy")
    if (
        runtime["schema"] != "whisper-cpp-cpu:1"
        or runtime["engine_version"] != VERSION
        or runtime["engine_revision"] != REVISION
        or runtime["device"] != "cpu"
        or type(runtime["threads"]) is not int
        or runtime["threads"] != 2
        or runtime["max_audio_seconds"] != MAX_AUDIO_SECONDS
        or runtime["timestamps"] is not True
        or type(options) is not dict
        or options.keys() - {"language", "timestamps"}
        or options.get("timestamps") is not True
        or options.get("language", "auto") != runtime["language"]
        or type(runtime["language"]) is not str
        or runtime["language"] not in LANGUAGES
    ):
        raise _error("plan", "ASR冻结计划或请求选项发生变化。", "policy")
    for key in ("engine", "model", "ffmpeg", "ffprobe"):
        identity = runtime[key]
        if type(identity) is not dict or set(identity) != {
            "path",
            "device",
            "inode",
            "bytes",
            "mtime_ns",
            "ctime_ns",
        }:
            raise _error("plan", "ASR组件身份记录无效。", "policy")
        if type(identity["path"]) is not str or any(
            type(identity[key]) is not int or identity[key] < 0
            for key in ("device", "inode", "bytes", "mtime_ns", "ctime_ns")
        ):
            raise _error("plan", "ASR组件身份字段无效。", "policy")
        try:
            actual = _identity(identity["path"], executable=key != "model", model=key == "model")
        except (OSError, ValueError):
            raise _error(
                "identity_changed", "ASR组件或模型已不可用，停止处理。", "conflict"
            ) from None
        if actual != identity:
            raise _error("identity_changed", "ASR组件或模型身份已变化，停止处理。", "conflict")


def transcribe(plan, source, output, work, result, *, run, require):
    """Run within the existing disposable CPU process and publish only candidates."""
    runtime, options = plan.get("asr_runtime"), plan.get("asr")
    _validate_frozen(runtime, options)
    suffix = Path(plan["filename"]).suffix.lower()
    if suffix not in AUDIO_SUFFIXES or not result["metadata"]["mime_type"].startswith("audio/"):
        raise _error(
            "audio_only", "当前ASR只接受已识别的音频原件；不从视频静默提取音轨。", "validation"
        )
    ffprobe, ffmpeg = runtime["ffprobe"]["path"], runtime["ffmpeg"]["path"]
    raw, _ = run(
        [
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            AUDIO_FORMATS,
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        work,
        timeout=10,
        stdout_limit=256 * 1024,
    )
    try:
        media = json.loads(raw)
        streams = media["streams"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        duration = float(media["format"]["duration"])
        valid = len(audio) == 1 and all(item.get("codec_type") == "audio" for item in streams)
        valid = valid and math.isfinite(duration) and 0 < duration <= MAX_AUDIO_SECONDS
        valid = valid and 1 <= int(audio[0]["channels"]) <= 2
    except (ValueError, TypeError, KeyError, IndexError):
        valid = False
    if not valid:
        raise _error(
            "audio_budget", "只支持时长明确且不超过120秒的单音轨、单声道/双声道音频。", "resource"
        )
    wav = work / "audio.wav"
    run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            AUDIO_FORMATS,
            "-threads",
            "2",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-threads",
            "2",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(wav),
        ],
        work,
        timeout=20,
    )
    try:
        with wave.open(str(wav), "rb") as audio_file:
            if (
                audio_file.getnchannels() != 1
                or audio_file.getsampwidth() != 2
                or audio_file.getframerate() != 16000
            ):
                raise ValueError
            decoded_duration = audio_file.getnframes() / 16000
        if not 0 < decoded_duration <= MAX_AUDIO_SECONDS:
            raise ValueError
    except (OSError, ValueError, wave.Error):
        raise _error("decode_invalid", "音频解码未产生预算内的16kHz单声道PCM。") from None
    _validate_frozen(runtime, options)
    prefix = work / "recognized"
    run(
        [
            runtime["engine"]["path"],
            "--model",
            runtime["model"]["path"],
            "--file",
            str(wav),
            "--no-gpu",
            "--threads",
            "2",
            "--processors",
            "1",
            "--language",
            runtime["language"],
            "--best-of",
            "1",
            "--beam-size",
            "1",
            "--no-fallback",
            "--output-json",
            "--output-file",
            str(prefix),
            "--no-prints",
        ],
        work,
        timeout=120,
        stdout_limit=2 * 1024**2,
    )
    _validate_frozen(runtime, options)
    engine_json = prefix.with_suffix(".json")
    if (
        not engine_json.is_file()
        or engine_json.is_symlink()
        or engine_json.stat().st_size > MAX_OUTPUT_BYTES
    ):
        raise _error("output_invalid", "ASR没有返回预算内的真实JSON分段结果。")
    try:
        payload = json.loads(engine_json.read_text(encoding="utf-8"))
        segments = payload["transcription"]
        language = payload["result"]["language"]
        if (
            type(segments) is not list
            or len(segments) > 2000
            or type(language) is not str
            or language not in LANGUAGES - {"auto"}
        ):
            raise ValueError
        parsed, length, previous = [], 0, 0
        for index, segment in enumerate(segments):
            text = segment["text"]
            start, end = segment["offsets"]["from"], segment["offsets"]["to"]
            if (
                type(text) is not str
                or type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end <= decoded_duration * 1000 + 1000
                or start < previous
            ):
                raise ValueError
            previous = start
            length += len(text)
            if length > 1_000_000:
                raise ValueError
            parsed.append(
                {
                    "text": text,
                    "locator": {
                        "audio_segment": index + 1,
                        "start_seconds": start / 1000,
                        "end_seconds": end / 1000,
                        "timestamp_source": "whisper.cpp.segment_offsets_ms",
                        "time_basis": "decoded_audio_zero_origin",
                    },
                }
            )
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise _error("output_invalid", "ASR分段文本或时间定位无效，未发布候选。") from None
    result["segments"] = parsed
    result["metadata"].update(
        {
            "asr_engine": "whisper.cpp",
            "asr_version": VERSION,
            "asr_revision": REVISION,
            "asr_device": "cpu",
            "asr_threads": 2,
            "asr_model_identity": {
                key: value for key, value in runtime["model"].items() if key != "path"
            },
            "requested_language": runtime["language"],
            "detected_language": language,
            "duration_seconds": decoded_duration,
            "source_container_duration_seconds": duration,
            "transcript_segments": len(parsed),
            "timestamps": "model_estimated_segment_offsets",
            "timestamp_tolerance_seconds": 1,
        }
    )
    result["complete"] = True
    result["warnings"].append(
        "ASR文本与时间戳为本地模型识别结果，需对照原音；不是人工确认的逐字记录。"
    )
    if not any(item["text"].strip() for item in parsed):
        result["warnings"].append("本次未产生非空转写；不据此断言原音没有语音。")
    document = {
        "segments": parsed,
        "metadata": result["metadata"],
        "warnings": result["warnings"],
        "complete": True,
    }
    contents = {
        "transcript.txt": ("\n".join(item["text"] for item in parsed)).encode("utf-8"),
        "transcript.json": json.dumps(document, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        ),
    }
    for name, content in contents.items():
        if len(content) > MAX_OUTPUT_BYTES:
            raise _error("output_budget", "ASR输出超过4 MiB预算，未发布。", "resource")
        with (output / name).open("xb") as target:
            os.chmod(output / name, 0o600)
            target.write(content)
        result["exports"].append(
            {
                "filename": name,
                "display_name": name,
                "mime_type": "text/plain; charset=utf-8"
                if name.endswith(".txt")
                else "application/json",
                "kind": "asr_text" if name.endswith(".txt") else "asr_segments",
                "source_refs": result["metadata"]["source_refs"],
            }
        )
