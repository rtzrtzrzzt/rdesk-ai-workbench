"""Bounded native format adapters. Extraction is distinct from native model input."""

import codecs
import csv
import importlib.metadata
import io
import json
import mimetypes
import shutil
import subprocess
import tarfile
import zipfile
from html.parser import HTMLParser
from itertools import islice
from pathlib import Path

from workbench.contracts.errors import AppError

MAX_TEXT = 1_000_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def detect_type(path: Path, filename: str, *, deep: bool = False) -> tuple[str, str]:
    with path.open("rb") as stream:
        header = stream.read(8192)
    if header.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if header.startswith(b"PK\x03\x04"):
        if not deep:
            return "application/zip", "zip"
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        for marker, kind, mime in (
            (
                "word/document.xml",
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                "xl/workbook.xml",
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "ppt/presentation.xml",
                "pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
            ("META-INF/container.xml", "epub", "application/epub+zip"),
        ):
            if marker in names:
                return mime, kind
        return "application/zip", "zip"
    if (
        header[:4] in {b"\x89PNG", b"GIF8", b"II*\x00", b"MM\x00*"}
        or header.startswith((b"\xff\xd8\xff", b"BM"))
        or header[8:12] == b"WEBP"
    ):
        mime = (
            "image/png"
            if header[:4] == b"\x89PNG"
            else "image/gif"
            if header[:4] == b"GIF8"
            else "image/tiff"
            if header[:4] in {b"II*\x00", b"MM\x00*"}
            else "image/jpeg"
            if header.startswith(b"\xff\xd8\xff")
            else "image/bmp"
            if header.startswith(b"BM")
            else "image/webp"
        )
        return mime, "image"
    if header.startswith(b"\x1f\x8b"):
        return "application/gzip", "gz"
    if header[257:262] == b"ustar":
        return "application/x-tar", "tar"
    suffix = Path(filename).suffix.lower().lstrip(".")
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if header[4:8] == b"ftyp" or header.startswith(
        (b"RIFF", b"ID3", b"OggS", b"fLaC", b"\x1aE\xdf\xa3")
    ):
        return mime, "media"
    try:
        encoding = "utf-16" if header.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = codecs.getincrementaldecoder(encoding)().decode(header, final=False)
        if "\x00" not in text:
            return (
                mime
                if mime.startswith("text/") or suffix in {"json", "xml", "svg"}
                else "text/plain"
            ), suffix or "text"
    except UnicodeError:
        pass
    return mime, "unknown"


def capabilities() -> list[dict]:
    entries = [
        (
            "text",
            "文本与结构文件",
            ["txt", "md", "csv", "tsv", "json", "jsonl", "toml", "xml", "html", "源码"],
            ["extract"],
            None,
            "按范围读取；HTML只提取正文，不执行脚本或加载外链。",
        ),
        (
            "pdf",
            "PDF 文本层",
            ["pdf"],
            ["extract"],
            "pypdf",
            "按页提取；不含OCR，不承诺表格、签章或版面无损。",
        ),
        (
            "docx",
            "Word 文档",
            ["docx"],
            ["extract"],
            "python-docx",
            "段落及表格定位；不包含复杂修订、浮动文本框或无损版面。",
        ),
        (
            "xlsx",
            "Excel 工作簿",
            ["xlsx"],
            ["extract"],
            "openpyxl",
            "工作表和单元格定位；公式保留为公式，不重算。",
        ),
        (
            "pptx",
            "PowerPoint 演示",
            ["pptx"],
            ["extract"],
            "python-pptx",
            "幻灯片和形状文本；不承诺动画或母版保真。",
        ),
        (
            "image",
            "位图预览",
            ["png", "jpeg", "webp", "tiff", "bmp", "gif"],
            ["preview"],
            "pillow",
            "只生成安全缩略图及尺寸，不代表已完成图片理解或OCR。",
        ),
        (
            "archive",
            "归档目录",
            ["zip", "tar", "gz"],
            ["extract"],
            None,
            "先列目录，不解包、不执行其中内容。",
        ),
        (
            "media",
            "音视频元数据",
            ["mp4", "webm", "wav", "mp3", "flac", "ogg"],
            ["extract"],
            None,
            "需本机ffprobe；只读真实元数据，不冒充转写或完整视听理解。",
        ),
    ]
    result = []
    for key, name, formats, operations, package, limitation in entries:
        version = importlib.metadata.version(package) if package else None
        status = "available" if key != "media" or shutil.which("ffprobe") else "not_installed"
        result.append(
            {
                "id": key,
                "name": name,
                "formats": formats,
                "operations": operations,
                "status": status,
                "version": version,
                "limitation": limitation,
            }
        )
    from workbench.modules.files.advanced import capabilities as advanced_capabilities

    return result + advanced_capabilities()


def check_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10_000 or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES:
            raise AppError(
                "archive_budget", "归档展开体积或条目数超过处理预算，请缩小范围。", 422, "resource"
            )
        for entry in entries:
            if (
                entry.file_size > 64 * 1024 * 1024
                or entry.file_size / max(1, entry.compress_size) > 1000
            ):
                raise AppError("archive_budget", "归档中存在超出预算的条目。", 422, "resource")


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored and data.strip():
            self.parts.append(data.strip())


def extract(
    path: Path, filename: str, start: int, limit: int, operation: str, output: Path
) -> dict:
    mime, kind = detect_type(path, filename, deep=True)
    warnings, segments = [], []
    metadata = {"format": kind, "mime_type": mime}
    complete = True

    def add(text, locator):
        nonlocal complete
        text = str(text)
        remaining = MAX_TEXT - sum(len(part["text"]) for part in segments)
        if remaining <= 0:
            complete = False
            return
        if len(text) > remaining:
            complete = False
            text = text[:remaining]
        segments.append({"text": text, "locator": locator})

    if operation == "preview":
        if kind != "image":
            raise AppError(
                "preview_not_supported", "此格式暂无图像预览适配器，可以提取文本或下载原件。", 422
            )
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(path) as source:
            metadata.update(
                width=source.width, height=source.height, frames=getattr(source, "n_frames", 1)
            )
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600))
            image.convert("RGB").save(output / "preview.png", format="PNG")
        return {
            "segments": [],
            "metadata": metadata,
            "warnings": ["预览不包含OCR或模型视觉理解。"],
            "complete": True,
            "preview": "preview.png",
        }
    if kind in {"docx", "xlsx", "pptx", "zip", "epub"}:
        check_archive(path)
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            raise AppError(
                "document_password_required", "PDF受密码保护，原件已保留；当前处理未解密。", 422
            )
        metadata["total_pages"] = len(reader.pages)
        for number in range(start - 1, min(start - 1 + limit, len(reader.pages))):
            page = reader.pages[number]
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 16 * 1024 * 1024:
                raise AppError(
                    "pdf_page_budget",
                    "PDF页面内容过大，请缩小范围或换用受控文档处理器。",
                    422,
                    "resource",
                )
            text = page.extract_text() or ""
            if not text.strip():
                warnings.append(f"第{number + 1}页没有可提取文本，可能需要OCR。")
            add(text, {"page": number + 1})
        complete = complete and start == 1 and limit >= len(reader.pages)
        warnings.append("文本提取不保留原始版面，不保证表格结构完整。")
    elif kind == "docx":
        from docx import Document
        from docx.table import Table

        document = Document(path)
        blocks = list(document.iter_inner_content())
        metadata["total_blocks"] = len(blocks)
        for index, block in enumerate(blocks[start - 1 : start - 1 + limit], start=start):
            text = (
                "\n".join("\t".join(cell.text for cell in row.cells) for row in block.rows)
                if isinstance(block, Table)
                else block.text
            )
            add(
                text, {"block": index, "type": "table" if isinstance(block, Table) else "paragraph"}
            )
        complete = complete and start == 1 and limit >= len(blocks)
        warnings.append("不包含复杂修订、文本框或页面排版；来源按文档块定位。")
    elif kind == "xlsx":
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            metadata["worksheets"] = book.sheetnames
            for sheet in book:
                for row in sheet.iter_rows(
                    min_row=start,
                    max_row=start + limit - 1,
                    max_col=min(sheet.max_column or 100, 100),
                ):
                    cells = [
                        {
                            "cell": cell.coordinate,
                            "value": str(cell.value),
                            "formula": cell.data_type == "f",
                        }
                        for cell in row
                        if cell.value is not None
                    ]
                    if cells:
                        add(
                            json.dumps(cells, ensure_ascii=False),
                            {"sheet": sheet.title, "row": row[0].row},
                        )
            complete = False
            warnings.append("只读指定行范围、每行最多100列；公式未重算，空缓存结果不伪造。")
        finally:
            book.close()
    elif kind == "pptx":
        from pptx import Presentation

        deck = Presentation(path)
        metadata["total_slides"] = len(deck.slides)
        for index in range(start - 1, min(start - 1 + limit, len(deck.slides))):
            for shape in deck.slides[index].shapes:
                if shape.has_text_frame:
                    add(shape.text, {"slide": index + 1, "shape_id": shape.shape_id})
                elif shape.has_table:
                    add(
                        "\n".join(
                            "\t".join(cell.text for cell in row.cells) for row in shape.table.rows
                        ),
                        {"slide": index + 1, "shape_id": shape.shape_id, "type": "table"},
                    )
        complete = complete and start == 1 and limit >= len(deck.slides)
        warnings.append("只提取文本/表格，不解析动画、母版或嵌入媒体。")
    elif kind in {"zip", "epub"}:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            metadata["total_entries"] = len(entries)
            for entry in entries[start - 1 : start - 1 + limit]:
                add(
                    entry.filename,
                    {
                        "entry": entry.filename,
                        "bytes": entry.file_size,
                        "compressed_bytes": entry.compress_size,
                    },
                )
            complete = complete and start == 1 and limit >= len(entries)
        warnings.append("这里只列出归档目录，没有解包或读取各条目正文。")
    elif kind in {"tar", "gz"}:
        if kind == "gz" and not tarfile.is_tarfile(path):
            add("GZIP单一压缩流，当前仅识别格式，未展开正文。", {"container": "gzip"})
            complete = False
        else:
            count = 0
            with tarfile.open(path, mode="r:*") as archive:
                for index, entry in enumerate(archive, start=1):
                    count = index
                    if index > 10_000:
                        raise AppError("archive_budget", "归档目录条目超过预算。", 422, "resource")
                    if start <= index < start + limit:
                        add(
                            entry.name,
                            {
                                "entry": entry.name,
                                "bytes": entry.size,
                                "link": entry.issym() or entry.islnk(),
                            },
                        )
                    if index >= start + limit:
                        complete = False
                        break
            metadata["observed_entries"] = count
        warnings.append("仅查看压缩包目录或容器元数据，没有解压或执行内容。")
    elif kind == "media":
        from workbench.modules.files.advanced import _require

        executable = _require("ffprobe")
        result = subprocess.run(
            [executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode or len(result.stdout) > 1_000_000:
            raise AppError("media_metadata_failed", "无法读取此媒体元数据，原件已保留。", 422)
        media = json.loads(result.stdout)
        media.get("format", {}).pop("filename", None)
        metadata["media"] = media
        warnings.append("仅提取元数据，未转写、抽帧或理解音视频内容。")
    elif kind == "unknown" or kind == "image":
        raise AppError(
            "file_processor_missing",
            "当前格式没有可用的文本提取器；图片可使用预览，原件仍保留。",
            422,
            "capability",
        )
    else:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise AppError(
                "text_read_budget", "此文本超过当前解析预算，请先选择更小文件。", 422, "resource"
            )
        data = path.read_bytes()
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = data.decode(encoding)
        except UnicodeError:
            raise AppError(
                "unsupported_encoding", "无法按UTF-8/UTF-16解码，原件已保留。", 422
            ) from None
        metadata["encoding"] = encoding
        if kind in {"html", "htm", "xhtml", "mhtml"}:
            parser = TextHTML()
            parser.feed(text)
            text = "\n".join(parser.parts)
            warnings.append("仅提取HTML可读文本，不执行脚本或请求外部资源；不保证MHTML资源完整。")
        elif kind == "json":
            json.loads(text)
        elif kind == "toml":
            import tomllib

            tomllib.loads(text)
        elif kind in {"xml", "svg"}:
            from defusedxml.ElementTree import fromstring

            fromstring(text)
            warnings.append("保留XML/SVG源码，不执行脚本或加载外部实体。")
        if kind in {"csv", "tsv"}:
            rows = csv.reader(io.StringIO(text), delimiter="\t" if kind == "tsv" else ",")
            for number, row in enumerate(islice(rows, start - 1, start - 1 + limit), start=start):
                add(json.dumps(row, ensure_ascii=False), {"record": number})
            complete = False
        else:
            lines = text.splitlines()
            metadata["total_lines"] = len(lines)
            add(
                "\n".join(lines[start - 1 : start - 1 + limit]),
                {"start_line": start, "end_line": min(len(lines), start + limit - 1)},
            )
            complete = complete and start == 1 and limit >= len(lines)
    if not complete:
        warnings.append("这是限定范围或容量内的部分结果，不能据此声称处理了全部原件。")
    return {"segments": segments, "metadata": metadata, "warnings": warnings, "complete": complete}
