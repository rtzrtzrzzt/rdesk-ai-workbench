"""Restricted file generation through existing upload + file-job publication.

Integration (no route/worker mutations in this module):
* create_export(db, user_id, workspace_id, ExportCreate) returns existing dto.FileTask.
  Requires a clean authentication/read Session; releases its read transaction. Uses
  the existing request advisory lock and independent short transactions. Inline
  user input is an immutable source JSON artifact via upload/chunks/complete;
  image input is an authorized, version-checked artifact. create_processing queues
  operation='export', start=limit=1, export_spec=<ExportCreate JSON>. Caller commit
  is unnecessary. Source publication and job acceptance are NOT one FS/DB atomic
  transaction: retry the same request to find the same source and accepted job.
* The file worker copies run.config_snapshot['export_spec'] into its internal plan
  (alongside its existing input/expected_hash/expected_bytes/output/artifact_id).
  In the existing resource-limited disposable processor, dispatch export plans to
  render_export(plan). It creates fixed export.<format> in existing staging and
  returns the existing result.json shape plus exports:[{filename,display_name,
  mime_type,kind}]. Publish this extra output with existing deterministic artifact
  IDs, parent_id=plan.artifact_id, and existing intent/publish/available protocol.
  Include processor metadata and source_refs. Preserve the existing source artifact.
* No HTTP request should decode images or construct Office documents. The existing
  file processor's 180s cancellation/120s CPU/2GiB address/32MiB file limits apply;
  this renderer additionally caps output16MiB, image input32MiB/16M pixels, JSON
  request2MiB and Office20k cells. Cancellation is the worker process's concern.

Supported generation is intentionally structural, not round-trip editing: UTF-8
TXT/Markdown/JSON, CSV with explicit formula policy, DOCX paragraphs/tables, XLSX
named sheets and explicit allowlisted formulas (not calculated), PPTX text/tables,
and Pillow PNG/JPEG/WebP conversion. Multi-frame images require explicit frame;
JPEG alpha is flattened to the requested background; image metadata is stripped.
No macros, templates, arbitrary code, external formulas or remote fetches execute.
Office-to-PDF is unavailable here: no libreoffice/soffice exists in the checked
host. Finding a binary later reports adapter_pending, never fake capability.

Official APIs checked against installed libraries: python-docx Document paragraphs
and tables https://python-docx.readthedocs.io/en/latest/user/quickstart.html ;
openpyxl cells/formulas (it never evaluates formulas)
https://openpyxl.readthedocs.io/en/stable/simple_formulae.html ; python-pptx text
and tables https://python-pptx.readthedocs.io/en/latest/user/quickstart.html ; Pillow
save/EXIF transpose https://pillow.readthedocs.io/en/stable/reference/Image.html .
Only existing dependencies are used. The two module files contain the integration
contract and limitations so no unrelated docs or existing worker is overwritten.

Auxiliary verification (2026-10-17): actual project docs paragraphs/table rendered
and read back as TXT/MD/CSV/JSON/DOCX/XLSX/PPTX in a cleaned temporary directory.
An explicit LEN(A1) formula referring to real document text remained a formula;
data_only returned None, not a fabricated result. External WEBSERVICE/DDE input
was rejected. No model output, user account, fake business record or paid request
was created. Pillow WebP encoder is present. Image conversion and the complete
queued export/publication/UI path await the main agent's real authorized input and
worker integration; library checks are not product acceptance.
"""

import csv
import hashlib
import importlib.metadata
import io
import json
import re
from pathlib import Path
from uuid import uuid5

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.locks import advisory_connection
from workbench.modules import workspace_service
from workbench.modules.files import dto
from workbench.modules.files import exports_dto as schemas
from workbench.modules.files import service as files
from workbench.modules.files.storage import file_digest, storage_path
from workbench.modules.runs.domain import ClientRequest, Run

MAX_OUTPUT = 16 * 1024**2
MAX_IMAGE_INPUT = 32 * 1024**2
MAX_PIXELS = 16_000_000
MIME = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def capabilities() -> list[dict]:
    result = []
    for formats, package in (
        (["txt", "md", "csv", "json"], None),
        (["docx"], "python-docx"),
        (["xlsx"], "openpyxl"),
        (["pptx"], "python-pptx"),
        (["png", "jpeg", "webp"], "pillow"),
    ):
        result.append(
            {
                "id": "export_" + formats[0],
                "name": "受限导出 / " + ", ".join(formats),
                "operations": ["export"],
                "formats": formats,
                "status": "available",
                "version": importlib.metadata.version(package) if package else None,
                "limitation": "受限结构导出；必须经现有文件作业执行与发布。",
            }
        )
    from workbench.modules.files.advanced import capabilities as advanced_capabilities

    office = next(item for item in advanced_capabilities() if item["id"] == "office_conversion")
    result.append(
        {
            "id": "export_pdf",
            "name": "Office / PDF 转换",
            "operations": ["export"] if office["status"] == "available" else [],
            "formats": ["pdf"],
            "status": office["status"],
            "version": office["version"],
            "limitation": office["limitation"],
        }
    )
    return result


def _references(db, user_id, workspace_id, command):
    references = list(command.sources)
    if isinstance(command.spec, (schemas.ImageConversion, schemas.PDFConversion)):
        references.append(command.spec.source)
    result = []
    for reference in references:
        artifact = files.get_artifact(db, user_id, workspace_id, str(reference.artifact_id))
        if artifact.status != "available" or artifact.content_hash != reference.expected_version:
            raise AppError(
                "export_source_conflict", "导出来源尚不可用或版本已经改变。", 409, "conflict"
            )
        result.append({"artifact_id": artifact.id, "version": artifact.content_hash})
    return result


def _source_upload(db, user_id, workspace_id, command):
    payload = json.dumps(
        {"workspace_id": workspace_id, "export_spec": command.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    upload = files.create_upload(
        db,
        user_id,
        workspace_id,
        dto.UploadCreate(
            filename=f"export-source-{digest}.json",
            total_bytes=len(payload),
            mime_type="application/json",
            request_id=uuid5(command.request_id, "export-source"),
        ),
    )
    db.commit()
    if upload["status"] != "completed":
        for offset in range(0, len(payload), files.CHUNK_SIZE):
            files.accept_chunk(
                user_id,
                workspace_id,
                upload["id"],
                offset,
                payload[offset : offset + files.CHUNK_SIZE],
            )
    result = files.complete_upload(user_id, workspace_id, upload["id"])
    if result["version"] != digest or result["byte_size"] != len(payload):
        raise AppError(
            "export_source_conflict",
            "已持久化导出输入与此请求不一致，停止发布。",
            409,
            "outcome_unknown",
        )
    return result["id"]


def create_export(
    db: Session,
    user_id: str,
    workspace_id: str,
    command: schemas.ExportCreate,
    *,
    actor_run_id: str | None = None,
) -> dto.FileTask:
    """Accept one durable file job, preserving actual source; never render in this request."""
    if db.new or db.dirty or db.deleted:
        raise AppError("export_session_dirty", "请先提交当前写入，再独立创建导出任务。", 409)
    workspace_service.get(db, user_id, workspace_id)
    db.rollback()
    with advisory_connection(f"{user_id}:{command.request_id}") as connection:
        with Session(bind=connection, expire_on_commit=False) as owned:
            workspace_service.get(owned, user_id, workspace_id)
            previous = owned.scalar(
                select(ClientRequest).where(
                    ClientRequest.user_id == user_id,
                    ClientRequest.request_id == str(command.request_id),
                )
            )
            if previous:
                if previous.expired:
                    raise AppError("request_expired", "导出请求已过期，请明确创建新的请求。", 410)
                run = owned.get(Run, previous.resource_id) if previous.resource_id else None
                if (
                    not run
                    or run.user_id != user_id
                    or run.workspace_id != workspace_id
                    or run.kind != "file"
                    or run.config_snapshot.get("export_spec") != command.model_dump(mode="json")
                ):
                    raise AppError("idempotency_conflict", "相同请求标识已经用于其他内容。", 409)
                return dto.FileTask.model_validate(files.task_view(owned, run))
            references = _references(owned, user_id, workspace_id, command)
            owned.rollback()
            source_id = (
                str(command.spec.source.artifact_id)
                if isinstance(command.spec, (schemas.ImageConversion, schemas.PDFConversion))
                else _source_upload(owned, user_id, workspace_id, command)
            )
            # Recheck source authorization/version after its durable publication.
            _references(owned, user_id, workspace_id, command)
            processing = schemas.ExportProcessing(
                request_id=command.request_id, export_spec=command
            )
            actor = None
            if actor_run_id:
                from workbench.modules.collaboration.service import lock_root

                _, actor = lock_root(owned, actor_run_id)
                if actor.cancel_requested_at:
                    raise AppError("run_finished", "任务已停止，不能创建导出任务。", 409)
            result = files.create_processing(
                owned, user_id, workspace_id, source_id, processing, actor=actor
            )
            run = owned.get(Run, result["id"])
            run.config_snapshot = {**run.config_snapshot, "export_source_refs": references}
            owned.commit()
            return dto.FileTask.model_validate(files.task_view(owned, run))


class _Buffer(io.BytesIO):
    def write(self, content):
        if self.tell() + len(content) > MAX_OUTPUT:
            raise AppError(
                "export_output_budget", "生成文件超过 16 MiB 输出预算。", 422, "resource"
            )
        return super().write(content)


def _xml_text(value):
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise AppError("export_text_invalid", "Office 文本含 XML 不支持的控制字符。", 422)
    return value


def _structured(spec, output):
    warnings = []
    if isinstance(spec, schemas.TextExport):
        output.write(spec.text.encode("utf-8"))
    elif isinstance(spec, schemas.JSONExport):
        output.write(
            (json.dumps(spec.value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        )
    elif isinstance(spec, schemas.CSVExport):
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\r\n")
        escaped = 0
        for row in spec.rows:
            cells = []
            for cell in row:
                if (
                    isinstance(cell, str)
                    and cell.lstrip().startswith(("=", "+", "-", "@"))
                    and spec.formula_policy == "escape"
                ):
                    cell = "'" + cell
                    escaped += 1
                cells.append(cell)
            writer.writerow(cells)
        output.write(text.getvalue().encode("utf-8"))
        warnings.append(f"CSV 公式策略为 {spec.formula_policy}；实际转义 {escaped} 个文本单元格。")
        if spec.formula_policy == "preserve":
            warnings.append("CSV 保留公式前缀；在电子表格软件打开前应核对内容。")
    elif isinstance(spec, schemas.DOCXExport):
        from docx import Document

        document = Document()
        document.core_properties.author = ""
        for paragraph in spec.paragraphs:
            document.add_paragraph(_xml_text(paragraph))
        for data in spec.tables:
            table = document.add_table(rows=len(data.rows), cols=len(data.rows[0]))
            for r, row in enumerate(data.rows):
                for c, value in enumerate(row):
                    table.cell(r, c).text = _xml_text(value)
        document.save(output)
        warnings.append("仅生成段落和表格，不保证复杂版式、分页或字体一致。")
    elif isinstance(spec, schemas.XLSXExport):
        from openpyxl import Workbook
        from openpyxl.workbook.properties import CalcProperties

        workbook = Workbook()
        workbook.remove(workbook.active)
        formulas = 0
        for data in spec.sheets:
            sheet = workbook.create_sheet(data.name)
            for r, row in enumerate(data.rows, start=1):
                for c, value in enumerate(row, start=1):
                    cell = sheet.cell(r, c)
                    if isinstance(value, schemas.FormulaCell):
                        cell.value = value.formula
                        formulas += 1
                    elif isinstance(value, str):
                        cell.value = _xml_text(value)
                        cell.data_type = "s"
                    else:
                        cell.value = value
        workbook.calculation = CalcProperties(
            calcMode="manual", fullCalcOnLoad=False, forceFullCalc=False
        )
        workbook.save(output)
        warnings.append(f"保留 {formulas} 个显式公式，未执行或计算；普通等号开头字符串仍是文本。")
    elif isinstance(spec, schemas.PPTXExport):
        from pptx import Presentation
        from pptx.util import Inches, Pt

        presentation = Presentation()
        presentation.core_properties.author = ""
        for data in spec.slides:
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            if data.title:
                frame = slide.shapes.add_textbox(
                    Inches(0.5), Inches(0.3), Inches(9), Inches(0.8)
                ).text_frame
                frame.text = _xml_text(data.title)
                frame.paragraphs[0].font.size = Pt(26)
            if data.paragraphs:
                frame = slide.shapes.add_textbox(
                    Inches(0.5), Inches(1.2), Inches(9), Inches(2.5 if data.table else 5.5)
                ).text_frame
                frame.word_wrap = True
                for index, value in enumerate(data.paragraphs):
                    paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                    paragraph.text = _xml_text(value)
                    paragraph.font.size = Pt(18)
            if data.table:
                rows = data.table.rows
                table = slide.shapes.add_table(
                    len(rows),
                    len(rows[0]),
                    Inches(0.5),
                    Inches(4 if data.paragraphs else 1.3),
                    Inches(9),
                    Inches(2.5),
                ).table
                for r, row in enumerate(rows):
                    for c, value in enumerate(row):
                        table.cell(r, c).text = _xml_text(value)
        presentation.save(output)
        warnings.append("固定简洁布局；长文本/大表格可能溢出，不保留动画、母版或嵌入媒体。")
    else:
        raise AppError("export_format_unsupported", "此格式没有受限导出适配器。", 422, "capability")
    return warnings


def _image(spec, path, output):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if path.stat().st_size > MAX_IMAGE_INPUT:
        raise AppError("export_image_budget", "图像原件超过 32 MiB。", 422, "resource")
    try:
        with Image.open(path) as source:
            frames = getattr(source, "n_frames", 1)
            if frames > 1 and spec.frame_index is None:
                raise AppError(
                    "export_frame_required",
                    "多帧图像必须明确选择 frame_index，不会静默丢弃其他帧。",
                    422,
                )
            frame = spec.frame_index or 0
            if frame >= frames:
                raise AppError("export_frame_invalid", "选择的图像帧不存在。", 422)
            source.seek(frame)
            if source.width * source.height > MAX_PIXELS:
                raise AppError(
                    "export_image_budget", "图像超过 1600 万像素解码预算。", 422, "resource"
                )
            image = ImageOps.exif_transpose(source)
            if spec.max_width or spec.max_height:
                image.thumbnail((spec.max_width or image.width, spec.max_height or image.height))
            if spec.format == "jpeg":
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, spec.jpeg_background)
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert(
                    "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                )
            image.info.clear()
            options = {"quality": spec.quality} if spec.format in {"jpeg", "webp"} else {}
            image.save(output, format=spec.format.upper(), **options)
            dimensions = {
                "width": image.width,
                "height": image.height,
                "selected_frame": frame,
                "source_frames": frames,
            }
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise AppError("export_image_invalid", "图像解码或编码失败；原件未修改。", 422) from None
    return ["转换不包含 OCR 或模型生成；图像元数据已移除，色彩/压缩可能变化。"], dimensions


def _managed_path(value):
    root = get_settings().data_dir.resolve()
    try:
        relative = Path(value).absolute().relative_to(root)
    except ValueError:
        raise AppError(
            "export_plan_invalid", "导出计划路径必须在受管文件区。", 422, "policy"
        ) from None
    return storage_path(relative.as_posix())


def render_export(plan: dict) -> dict:
    """Worker-only renderer: uses validated owned plan paths and does not publish artifacts."""
    try:
        command = schemas.ExportCreate.model_validate(plan["export_spec"])
    except (KeyError, ValidationError):
        raise AppError("export_plan_invalid", "导出计划不符合受限 schema。", 422) from None
    source = _managed_path(plan["input"])
    output_dir = _managed_path(plan["output"])
    if (
        source.stat().st_size != plan["expected_bytes"]
        or file_digest(source) != plan["expected_hash"]
    ):
        raise AppError("artifact_version_changed", "导出原件发生变化，停止处理。", 409, "conflict")
    if isinstance(command.spec, schemas.PDFConversion):
        from workbench.modules.files.advanced import dispatch

        result = dispatch({**plan, "operation": "convert_pdf"})
        result["exports"][0]["display_name"] = command.filename
        return result
    if not isinstance(command.spec, schemas.ImageConversion):
        if source.stat().st_size > 3 * 1024**2:
            raise AppError("export_source_budget", "导出结构化输入超过预算。", 422, "resource")
        original = json.loads(source.read_text(encoding="utf-8"))
        if original.get("export_spec") != command.model_dump(mode="json"):
            raise AppError(
                "export_source_conflict", "导出计划与持久化输入不一致。", 409, "conflict"
            )
    output = _Buffer()
    dimensions = {}
    if isinstance(command.spec, schemas.ImageConversion):
        warnings, dimensions = _image(command.spec, source, output)
    else:
        warnings = _structured(command.spec, output)
    filename = "export." + command.spec.format
    # New attempt staging only. Never replace an existing candidate or an original.
    with (output_dir / filename).open("xb") as stream:
        stream.write(output.getvalue())
    return {
        "segments": [],
        "metadata": {
            "format": command.spec.format,
            "mime_type": MIME[command.spec.format],
            "processor_revision": "native-export:1",
            "source_artifact_id": plan.get("artifact_id"),
            "source_version": plan["expected_hash"],
            "source_refs": [reference.model_dump(mode="json") for reference in command.sources],
            **dimensions,
        },
        "warnings": warnings,
        "complete": True,
        "exports": [
            {
                "filename": filename,
                "display_name": command.filename,
                "mime_type": MIME[command.spec.format],
                "kind": "export",
            }
        ],
    }
