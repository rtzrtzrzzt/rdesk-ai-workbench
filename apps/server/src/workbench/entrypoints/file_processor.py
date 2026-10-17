"""One bounded parse in a disposable native process; never connects to the database."""

import json
import os
import sys
from pathlib import Path


def main():
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    from workbench.contracts.errors import AppError
    from workbench.modules.files.processors import extract
    from workbench.modules.files.storage import file_digest

    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # Non-secret location only; the subprocess never receives the DB/provider environment.
    os.environ["WORKBENCH_DATA_DIR"] = plan["data_root"]
    output = Path(plan["output"])
    try:
        source = Path(plan["input"])
        if (
            source.stat().st_size != plan["expected_bytes"]
            or file_digest(source) != plan["expected_hash"]
        ):
            raise AppError(
                "artifact_version_changed",
                "原件存储内容已改变，停止处理并保留现有证据。",
                409,
                "conflict",
            )
        if plan["operation"] == "export":
            from workbench.modules.files.exports import render_export

            result = render_export(plan)
        elif plan["operation"] in {"ocr", "convert_pdf", "video_frames", "transcribe"}:
            from workbench.modules.files.advanced import dispatch

            result = dispatch(plan)
        else:
            result = extract(
                Path(plan["input"]),
                plan["filename"],
                plan["start"],
                plan["limit"],
                plan["operation"],
                output,
            )
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps({"status": "completed"}))
    except AppError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}))
        raise SystemExit(2) from None
    except MemoryError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "processor_memory_budget",
                        "message": "处理器达到内存预算，原件已保留，请缩小范围。",
                    }
                }
            )
        )
        raise SystemExit(2) from None
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "file_parse_failed",
                        "message": "文件解析失败，原件已保留。",
                        "category": type(error).__name__,
                    }
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
