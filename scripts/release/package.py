#!/usr/bin/env python3
"""Build/validate/stage a native SOURCE release; never install, migrate or activate."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import platform
import re
import stat
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath

if sys.version_info < (3, 12):  # noqa: UP036 -- executable bootstrap may use older Python
    raise SystemExit("Release tooling requires Python 3.12 or newer.")
import tomllib

from sbom import inventory

ROOT = Path(__file__).resolve().parents[2]
MAX_TOTAL, MAX_FILE, MAX_FILES = 256 * 1024**2, 64 * 1024**2, 10000
MANIFEST, SBOM = "RELEASE-MANIFEST.json", "SBOM.cdx.json"
REQUIRED = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "uv.lock",
    "alembic.ini",
    "start.sh",
    "start.ps1",
    "stop.sh",
    "stop.ps1",
    "apps/web/package.json",
    "apps/web/package-lock.json",
    "apps/web/index.html",
    "apps/web/vite.config.ts",
    "apps/web/tsconfig.json",
    "apps/web/dist/index.html",
)
TREES = {
    "apps/server/src/workbench": {".py", ".md", ".json"},
    "apps/server/migrations": {".py", ".mako"},
    "apps/web/src": None,
    "apps/web/public": None,
    "apps/web/dist": None,
    "scripts": {".py", ".sh", ".ps1", ".md"},
    "docs": {".md"},
    "runtime/installation": {".md"},
    "runtime/distribution": {".md", ".json"},
    "runtime/file-processing": {".md"},
    "runtime/sandbox": {".md"},
    "runtime/model": {".md", ".txt", ".in", ".lock"},
}
BANNED_PARTS = {
    "__pycache__",
    "node_modules",
    ".git",
    ".local",
    ".runtime",
    ".tools",
    ".venv",
    ".agents",
    ".codex",
}
EXECUTABLE = {"start.sh", "scripts/bootstrap.py", "scripts/release/package.py"}


class ReleaseError(RuntimeError):
    pass


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    probe = path
    while True:
        if probe.is_symlink():
            raise ReleaseError("Symlink inputs/parents are not accepted.")
        if probe == probe.parent:
            break
        probe = probe.parent
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise ReleaseError("Release input type/size is outside the file budget.")
    return info


def read_stable(path):
    before = regular(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    after = regular(path)
    if len(data) > MAX_FILE or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ReleaseError("A release input changed while reading; retry after edits finish.")
    return data


def mode(name):
    return 0o755 if name in EXECUTABLE or name.endswith(".sh") else 0o644


def allowed(name):
    parts = PurePosixPath(name).parts
    return (
        parts[0] != "models"
        and not any(p in BANNED_PARTS or p.startswith(".") for p in parts)
        and not name.endswith((".pyc", ".tsbuildinfo", ".pem", ".key"))
    )


def distributable(name):
    if not allowed(name):
        return False
    if name in REQUIRED or name in {"README.md", SBOM}:
        return True
    if name.startswith("THIRD_PARTY_LICENSES/npm/"):
        return bool(
            re.match(r"^(licen[cs]e|copying|notice)([._-].*)?$", PurePosixPath(name).name, re.I)
        )
    return any(
        name.startswith(directory + "/")
        and (extensions is None or PurePosixPath(name).suffix in extensions)
        for directory, extensions in TREES.items()
    )


def sources():
    paths = set(REQUIRED)
    if (ROOT / "README.md").is_file():
        paths.add("README.md")
    for directory, extensions in TREES.items():
        base = ROOT / directory
        if not base.is_dir():
            if directory.endswith(("/public", "/distribution")):
                continue
            raise ReleaseError("A required source directory is missing.")
        for path in base.rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if (
                allowed(relative)
                and path.is_file()
                and (extensions is None or path.suffix in extensions)
            ):
                paths.add(relative)
    return {name: read_stable(ROOT / name) for name in sorted(paths)}


def schema_heads(payload):
    revisions, parents = set(), set()
    for name, data in payload.items():
        if name.startswith("apps/server/migrations/versions/") and name.endswith(".py"):
            for node in ast.parse(data).body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in {
                            "revision",
                            "down_revision",
                        }:
                            value = ast.literal_eval(node.value)
                            if target.id == "revision":
                                revisions.add(value)
                            elif value:
                                parents.update(
                                    value if isinstance(value, (tuple, list)) else [value]
                                )
    return sorted(revisions - parents)


def build(output_dir, epoch):
    payload = sources()
    uv = tomllib.loads(payload["uv.lock"].decode())
    project = tomllib.loads(payload["pyproject.toml"].decode())["project"]
    npm = json.loads(payload["apps/web/package-lock.json"])
    evidence = {}

    def material(path):
        data = read_stable(path)
        evidence[path] = sha(data)
        return data

    components, edges, materials = inventory(
        ROOT, uv, npm, payload["THIRD_PARTY_NOTICES.md"].decode(), material
    )
    payload.update(materials)
    model_reference = json.loads(payload["runtime/distribution/external-components.json"])[
        "local_model_reference"
    ]
    snapshot = sha(encoded({name: sha(data) for name, data in sorted(payload.items())}))
    version = project["version"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", version):
        raise ReleaseError("Unsafe project version.")
    release_digest = sha(
        encoded(
            {
                "snapshot": snapshot,
                "epoch": epoch,
                "python": platform.python_version(),
                "zlib": zlib.ZLIB_VERSION,
                "external_model_reference": model_reference,
            }
        )
    )
    release_id = f"ai-workbench-{version}-source-{release_digest[:12]}"
    if model_reference and model_reference.get("model_revision"):
        model_ref = "external-model:" + model_reference["model_revision"]
        components.append(
            {
                "type": "machine-learning-model",
                "bom-ref": model_ref,
                "name": "Qwen3.8-Flash-Next-NVFP4 external reference",
                "version": model_reference["model_revision"],
                "properties": [
                    {
                        "name": "workbench:distribution",
                        "value": "external_not_bundled; license/installation separate",
                    },
                    {
                        "name": "workbench:reference",
                        "value": json.dumps(model_reference, sort_keys=True),
                    },
                ],
            }
        )
        edges[model_ref] = []
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "ai-workbench",
                "name": project["name"],
                "version": version,
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
            "properties": [
                {"name": "workbench:source_snapshot_sha256", "value": snapshot},
                {
                    "name": "workbench:scope",
                    "value": (
                        "source + existing web assets; locked dependencies "
                        "are not an installed-platform SBOM"
                    ),
                },
            ],
        },
        "components": components
        + [
            {
                "type": "application",
                "bom-ref": "ai-workbench-web-static",
                "name": "ai-workbench-web-static",
                "version": npm["version"],
                "properties": [
                    {
                        "name": "workbench:distribution",
                        "value": "bundled; apps/web/dist; exact files in RELEASE-MANIFEST.json",
                    },
                    {
                        "name": "workbench:provenance",
                        "value": "prebuilt local artifacts; source-to-bundle graph not available",
                    },
                ],
            }
        ],
        "dependencies": [
            {"ref": ref, "dependsOn": targets} for ref, targets in sorted(edges.items())
        ],
    }
    payload[SBOM] = encoded(bom)
    manifest = {
        "schema_version": 1,
        "distribution_kind": "native_source",
        "release_id": release_id,
        "application_version": version,
        "api_version": "v1",
        "database_schema_heads": schema_heads(payload),
        "checkpoint_compatibility": (
            "not independently verified; never auto-resume an incompatible checkpoint"
        ),
        "source_snapshot_sha256": snapshot,
        "source_date_epoch": epoch,
        "builder": {"python": platform.python_version(), "zlib": zlib.ZLIB_VERSION},
        "platforms": {
            "linux_aarch64": "source/staging auxiliary checks only; full release install pending",
            "windows": "waiting_real_environment_validation",
            "macos": "waiting_real_environment_validation",
        },
        "offline_complete": False,
        "build_backend_requirements": tomllib.loads(payload["pyproject.toml"].decode())
        .get("build-system", {})
        .get("requires", []),
        "build_backend_separately_locked": False,
        "external_requirements": [
            "Python3.12",
            "uv>=0.9.25 + locked PyPI packages",
            "native PostgreSQL16 or explicitly configured compatible instance",
            "model weights/engine if local inference selected",
            "Chrome/Playwright optional components",
            "Office/OCR/ASR/media native components when selected",
        ],
        "excluded": sorted(
            BANNED_PARTS
            | {"runtime/model/deployment.json", "credentials", "user data", "agent temporary state"}
        ),
        "external_model_reference_not_bundled": model_reference,
        "frontend": {
            "bundled": True,
            "node_required_for_existing_bundle": False,
            "rebuild_requires": "Node>=22.12/npm>=9.5",
            "build_provenance": "existing dist snapshot, not rebuilt or source-mapped by this tool",
        },
        "upgrade": {
            "verified_upgrade_from": [],
            "backup_required": True,
            "quiescence_required": True,
            "changes_schema_if_migrate_selected": True,
            "automatic_activation": False,
            "automatic_database_rollback": False,
            "failure_recovery": (
                "keep stopped; restore matching real backup to a separate database/data directory"
            ),
        },
        "integrity_boundary": (
            "payload hashes below; this manifest is covered by the outer archive SHA256, "
            "which is not a signature"
        ),
        "files": [
            {"path": name, "bytes": len(data), "sha256": sha(data), "mode": mode(name)}
            for name, data in sorted(payload.items())
        ],
    }
    payload[MANIFEST] = encoded(manifest)
    if len(payload) > MAX_FILES or sum(map(len, payload.values())) > MAX_TOTAL:
        raise ReleaseError("Source release exceeds the file/total budget.")
    # Current-worktree edits are not a global snapshot. Reject any source change
    # we can observe across capture and finalization rather than silently repack.
    for name, data in payload.items():
        if name not in {MANIFEST, SBOM} and not name.startswith("THIRD_PARTY_LICENSES/"):
            if sha(read_stable(ROOT / name)) != sha(data):
                raise ReleaseError("Source changed during packaging; no release published.")
    for path, digest in evidence.items():
        if sha(read_stable(path)) != digest:
            raise ReleaseError("License material changed during packaging.")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = output_dir / release_id
    if target.exists() or target.is_symlink():
        raise ReleaseError("Release directory already exists; no existing release is overwritten.")
    date = time.gmtime(max(315532800, epoch))[:6]
    with tempfile.TemporaryDirectory(prefix=".release-build-", dir=output_dir) as temporary:
        archive = Path(temporary) / f"{release_id}.zip"
        with zipfile.ZipFile(
            archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as package:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(f"{release_id}/{name}", date_time=date)
                info.create_system, info.external_attr = 3, (stat.S_IFREG | mode(name)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                package.writestr(info, data, compresslevel=9)
        package_digest = sha(archive.read_bytes())
        validate(archive, package_digest)
        target.mkdir(mode=0o700)
        archive_name = archive.name
        os.replace(archive, target / archive_name)
        (target / f"{archive_name}.sha256").write_text(
            f"{package_digest}  {archive_name}\n", encoding="ascii"
        )
        report = {
            "release_id": release_id,
            "archive": archive_name,
            "sha256": package_digest,
            "bytes": (target / archive_name).stat().st_size,
            "files": len(payload),
            "payload_bytes": sum(map(len, payload.values())),
            "python_locked": len([c for c in components if c["bom-ref"].startswith("pkg:pypi/")]),
            "npm_locked": len([c for c in components if c["bom-ref"].startswith("npm:")]),
            "license_files": len(materials),
            "activated": False,
        }
        (target / "BUILD-REPORT.json").write_bytes(encoded(report))
    return {**report, "directory": str(target)}


def safe_member(value):
    path = PurePosixPath(value)
    if (
        not value
        or any(ord(c) < 32 for c in value)
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or len(value) > 1024
    ):
        raise ReleaseError("Archive path is not a safe relative source path.")
    if path.as_posix() != value or any(
        p in {"", ".", ".."} or p.endswith((".", " ")) for p in path.parts
    ):
        raise ReleaseError("Archive path is not canonical.")
    if any(
        p.split(".", 1)[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *{f"COM{i}" for i in range(1, 10)},
            *{f"LPT{i}" for i in range(1, 10)},
        }
        for p in path.parts
    ):
        raise ReleaseError("Archive path uses a platform-reserved name.")
    return path


def validate(archive, expected, *, captured=None):
    data = read_stable(archive) if captured is None else captured
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha(data) != expected:
        raise ReleaseError("Archive SHA256 does not match the supplied review digest.")
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        entries = package.infolist()
        if not entries or len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_TOTAL:
            raise ReleaseError("Archive entry/total budget exceeded.")
        names, roots = {}, set()
        for entry in entries:
            path = safe_member(entry.filename)
            if (
                entry.is_dir()
                or stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG
                or entry.file_size > MAX_FILE
                or entry.flag_bits & 1
            ):
                raise ReleaseError("Only bounded unencrypted regular source files are accepted.")
            if entry.filename.casefold() in names:
                raise ReleaseError("Duplicate/case-colliding archive path.")
            names[entry.filename.casefold()] = entry
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise ReleaseError("Archive must contain one version directory.")
        root = roots.pop()
        if not re.fullmatch(r"ai-workbench-[A-Za-z0-9.+_-]+-source-[0-9a-f]{12}", root):
            raise ReleaseError("Unsupported release directory identifier.")
        manifest_name = f"{root}/{MANIFEST}"
        if (
            manifest_name.casefold() not in names
            or names[manifest_name.casefold()].file_size > 4 * 1024**2
        ):
            raise ReleaseError("Bounded release manifest missing.")
        manifest = json.loads(package.read(manifest_name))
        if (
            manifest.get("schema_version") != 1
            or manifest.get("distribution_kind") != "native_source"
            or manifest.get("release_id") != root
        ):
            raise ReleaseError("Unsupported source release manifest.")
        expected_names = {manifest_name}
        for item in manifest["files"]:
            relative = safe_member(item["path"]).as_posix()
            if not distributable(relative):
                raise ReleaseError("Excluded runtime/user-data path in archive manifest.")
            name = f"{root}/{relative}"
            if name in expected_names:
                raise ReleaseError("Duplicate manifest item.")
            expected_names.add(name)
            entry = names.get(name.casefold())
            if (
                entry is None
                or entry.filename != name
                or entry.file_size != item["bytes"]
                or item["mode"] not in {0o644, 0o755}
                or (entry.external_attr >> 16) & 0o777 != item["mode"]
            ):
                raise ReleaseError("Manifest metadata does not match the archive.")
            digest = hashlib.sha256()
            with package.open(entry) as stream:
                total = 0
                while chunk := stream.read(65536):
                    total += len(chunk)
                    if total > item["bytes"]:
                        raise ReleaseError("Archive data exceeds its declared size.")
                    digest.update(chunk)
            if total != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise ReleaseError("Payload hash mismatch.")
        if expected_names != {e.filename for e in entries}:
            raise ReleaseError("Unlisted archive files were not accepted.")
        if not set(REQUIRED).issubset({item["path"] for item in manifest["files"]}):
            raise ReleaseError("Required source/bootstrap files missing.")
    return manifest


def stage(archive, destination, expected):
    captured = read_stable(archive)
    manifest = validate(archive, expected, captured=captured)
    probe = destination.absolute()
    while probe != probe.parent:
        if probe.is_symlink():
            raise ReleaseError("Stage destination cannot traverse a symlink.")
        probe = probe.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)  # Never overwrite an existing or running version.
    try:
        with zipfile.ZipFile(io.BytesIO(captured)) as package:
            for entry in package.infolist():
                target = destination / safe_member(entry.filename)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    while chunk := source.read(65536):
                        output.write(chunk)
                os.chmod(target, (entry.external_attr >> 16) & 0o777)
        result = {
            "status": "staged_not_activated",
            "release_id": manifest["release_id"],
            "archive_sha256": expected,
            "version_directory": str(destination / manifest["release_id"]),
            "database_changed": False,
        }
        (destination / "STAGE-RECEIPT.json").write_bytes(encoded(result))
        return result
    except Exception:
        (destination / "STAGE-FAILED.json").write_bytes(
            encoded({"status": "incomplete_do_not_activate", "database_changed": False})
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser(
        "build", help="capture current approved source/assets; no install or web rebuild"
    )
    build_parser.add_argument("--output-dir", type=Path, default=ROOT / ".local/releases")
    build_parser.add_argument(
        "--source-date-epoch", type=int, default=int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    )
    for command in ("validate", "stage"):
        child = sub.add_parser(command)
        child.add_argument("archive", type=Path)
        child.add_argument(
            "--sha256", required=True, help="digest from the reviewable build report/trusted source"
        )
        if command == "stage":
            child.add_argument(
                "--destination",
                type=Path,
                required=True,
                help="NEW directory only; does not switch launchers",
            )
    args = parser.parse_args()
    if args.command == "build":
        if not 0 <= args.source_date_epoch <= 4102444799:
            raise ReleaseError("SOURCE_DATE_EPOCH out of supported ZIP timestamp range.")
        result = build(args.output_dir.absolute(), args.source_date_epoch)
    elif args.command == "validate":
        manifest = validate(args.archive, args.sha256)
        result = {
            "status": "validated_not_activated",
            "release_id": manifest["release_id"],
            "files": len(manifest["files"]) + 1,
        }
    else:
        result = stage(args.archive, args.destination.absolute(), args.sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        # Do not echo archive content, environment, credentials or arbitrary input.
        message = (
            str(error)
            if isinstance(error, ReleaseError)
            else "Invalid/incomplete source release input; no activation or database change."
        )
        print("Native source release: " + message, file=sys.stderr)
        raise SystemExit(2) from None
