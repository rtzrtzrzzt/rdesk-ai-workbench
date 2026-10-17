#!/usr/bin/env python3
"""Prepare native file components in this project; never install system packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".tools/file-components"
PREFIX = TOOLS / "root"
LOCK = TOOLS / "packages.json"
TARGETS = {
    "ocr": ["tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-chi-sim", "tesseract-ocr-chi-tra"],
    "office": ["libreoffice-writer", "libreoffice-calc", "libreoffice-impress"],
}
ASR_REVISION = "d1be6fde11ac6e0407606b4e42fe72d34add8037"
ASR_VERSION = "1.9.5"
ASR_ROOT = ROOT / ".tools/asr"
ASR_SOURCE = ASR_ROOT / ("whisper.cpp-" + ASR_REVISION)
ASR_FLAGS = [
    "-DCMAKE_BUILD_TYPE=Release",
    "-DBUILD_SHARED_LIBS=OFF",
    "-DWHISPER_BUILD_IS_DEV=OFF",
    "-DWHISPER_BUILD_TESTS=OFF",
    "-DWHISPER_BUILD_EXAMPLES=ON",
    "-DWHISPER_BUILD_SERVER=OFF",
    "-DWHISPER_CURL=OFF",
    "-DGGML_NATIVE=OFF",
    "-DGGML_CUDA=OFF",
    "-DGGML_METAL=OFF",
    "-DGGML_VULKAN=OFF",
    "-DGGML_OPENCL=OFF",
    "-DGGML_BLAS=OFF",
    "-DGGML_OPENMP=OFF",
    "-DGGML_RPC=OFF",
]


def asr_plan():
    return {
        "component": "asr",
        "engine": "whisper.cpp",
        "version": ASR_VERSION,
        "revision": ASR_REVISION,
        "license": "MIT",
        "device": "cpu",
        "build_jobs": 2,
        "source": "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/" + ASR_REVISION,
        "root": str(ASR_ROOT),
        "cmake_flags": ASR_FLAGS,
        "requirements": {name: bool(shutil.which(name)) for name in ("cmake", "c++")},
        "download_bytes": None,
        "model_download": False,
        "limitation": "只构建原生CPU引擎；不安装Python包、不下载模型、不执行音频推理。",
    }


def _asr_build_step(arguments, log, environment):
    process = subprocess.Popen(
        arguments,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 900
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise RuntimeError("ASR engine build exceeded 900 seconds; no model was loaded.")
            if sys.platform.startswith("linux"):
                fields = dict(
                    line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
                )
                if int(fields["MemAvailable"].strip().split()[0]) * 1024 < 8 * 1024**3:
                    raise RuntimeError("ASR engine build stopped: available memory below 8 GiB.")
            time.sleep(0.25)
        if process.returncode:
            raise RuntimeError("ASR engine build failed; inspect its private build log.")
    finally:
        if process.poll() is None:
            import signal

            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def prepare_asr():
    if os.name == "nt":
        raise RuntimeError(
            "This CPU source preparation helper requires a POSIX build host; "
            "Windows build remains unverified."
        )
    plan = asr_plan()
    if not all(plan["requirements"].values()):
        raise RuntimeError(
            "ASR source build requires an explicitly installed native CMake/C++ toolchain."
        )
    ASR_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ASR_ROOT, 0o700)
    if shutil.disk_usage(ASR_ROOT).free < 1024**3:
        raise RuntimeError("ASR source preparation requires at least 1 GiB free temporary space.")
    archive = ASR_ROOT / (ASR_REVISION + ".tar.gz")
    if not archive.exists():
        temporary = archive.with_suffix(".incomplete")
        try:
            with urlopen(plan["source"], timeout=60) as response, temporary.open("xb") as target:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024**2:
                        raise RuntimeError(
                            "Official ASR source archive exceeded 64 MiB preparation budget."
                        )
                    target.write(chunk)
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    if not ASR_SOURCE.exists():
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=ASR_ROOT) as directory:
            with tarfile.open(archive, "r:gz") as source:
                members = source.getmembers()
                if len(members) > 10000 or sum(m.size for m in members) > 512 * 1024**2:
                    raise RuntimeError("ASR source archive exceeded extraction budget.")
                source.extractall(directory, filter="data")
            children = list(Path(directory).iterdir())
            if len(children) != 1 or not (children[0] / "CMakeLists.txt").is_file():
                raise RuntimeError("Official ASR source archive structure is not recognized.")
            children[0].rename(ASR_SOURCE)
    build = ASR_SOURCE / "build-cpu"
    log_dir = ROOT / ".local/asr"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / ("build-" + str(time.time_ns()) + ".log")
    environment = {"PATH": os.defpath, "HOME": str(ASR_ROOT), "LANG": "C.UTF-8"}
    started = time.monotonic()
    with log_path.open("xb") as log:
        os.chmod(log_path, 0o600)
        _asr_build_step(
            ["cmake", "-S", str(ASR_SOURCE), "-B", str(build), *ASR_FLAGS], log, environment
        )
        _asr_build_step(
            ["cmake", "--build", str(build), "--target", "whisper-cli", "--parallel", "2"],
            log,
            environment,
        )
    engine = build / "bin/whisper-cli"
    version = run([engine, "--version"], env=environment).strip()
    if version != "whisper.cpp version: " + ASR_VERSION:
        raise RuntimeError("ASR engine did not report the pinned release version.")
    help_result = subprocess.run(
        [str(engine), "--help"], capture_output=True, timeout=10, env=environment
    )
    help_text = help_result.stdout + help_result.stderr
    for flag in (b"--output-json", b"--no-gpu", b"--language", b"--model", b"--file"):
        if flag not in help_text:
            raise RuntimeError("ASR engine help is missing a required official CLI option.")
    result = {
        **plan,
        "status": "engine_prepared",
        "engine_path": str(engine),
        "version_output": version,
        "download_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "actual_bytes": sum(
            p.stat().st_size for p in ASR_ROOT.rglob("*") if p.is_file() and not p.is_symlink()
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "log_path": str(log_path),
        "help_checked": True,
        "inference_verified": False,
    }
    destination = ASR_ROOT / "manifest.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return result


def run(args, **kwargs):
    result = subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, timeout=600, **kwargs
    )
    if result.returncode:
        raise RuntimeError(
            f"{Path(str(args[0])).name} failed ({result.returncode}); "
            "no system installation was performed."
        )
    return result.stdout


def resolve(component):
    """Ubuntu python-apt solves dependency alternatives using installed host libraries."""
    if platform.system() != "Linux" or "ID=ubuntu\n" not in Path("/etc/os-release").read_text():
        raise RuntimeError(
            "Private package preparation requires Ubuntu; "
            "use a native system installation elsewhere."
        )
    program = """import apt,apt_pkg,json,sys
apt_pkg.config.set('APT::Install-Recommends','false')
c=apt.Cache()
names=json.loads(sys.argv[1])
for name in names: c[name].mark_install(auto_fix=True,auto_inst=True,from_user=True)
selected={p.name:p for p in c.get_changes() if p.marked_install or p.marked_upgrade}
selected.update({name:c[name] for name in names})
print(json.dumps([
 {'name':p.name,'version':p.candidate.version,'architecture':p.candidate.architecture,
 'download_bytes':p.candidate.size,'installed_bytes':p.candidate.installed_size,
 'uri':p.candidate.uri}
 for p in sorted(selected.values(),key=lambda p:p.name)]))
"""
    packages = json.loads(run(["/usr/bin/python3", "-c", program, json.dumps(TARGETS[component])]))
    # Default Ubuntu repositories, including the host's configured official ESM,
    # are the only source. Do not silently use a PPA or another distribution.
    from urllib.parse import urlsplit

    for package in packages:
        host = urlsplit(package["uri"]).hostname or ""
        if host not in {
            "ports.ubuntu.com",
            "archive.ubuntu.com",
            "security.ubuntu.com",
            "esm.ubuntu.com",
        }:
            raise RuntimeError("Candidate is not from an official default Ubuntu repository.")
    if component == "ocr":
        engine = next(p for p in packages if p["name"] == "tesseract-ocr")
        if engine["version"] == "5.3.4-1build5":
            raise RuntimeError(
                "Public noble Tesseract lacks the configured ESM security fix; no fallback."
            )
    return packages


def relocate_office():
    """Relocate only extracted package config/symlinks, never the system copy."""
    program = PREFIX / "usr/lib/libreoffice/program"
    config = program / "fundamentalrc"
    text = config.read_text(encoding="utf-8")
    for location in ("/usr/lib/libreoffice", "/etc/libreoffice/registry"):
        text = text.replace("file://" + location, (PREFIX / location.lstrip("/")).as_uri())
    # Ubuntu's postinst normally copies .registry into /etc with ucf. We never
    # run maintainer scripts: bind directly to the extracted packaged registry.
    registry = (PREFIX / "usr/lib/libreoffice/share/.registry").as_uri()
    text = text.replace((PREFIX / "etc/libreoffice/registry").as_uri(), registry)
    text = text.replace(" dconf:*", "")
    config.write_text(text, encoding="utf-8")
    for link in (PREFIX / "usr/lib/libreoffice").rglob("*"):
        if link.is_symlink():
            target = os.readlink(link)
            if target.startswith("/"):
                replacement = PREFIX / target.lstrip("/")
                link.unlink()
                link.symlink_to(os.path.relpath(replacement, link.parent))
    for relative in (
        "var/lib/libreoffice/share/prereg/bundled",
        "var/spool/libreoffice/uno_packages/cache",
    ):
        (PREFIX / relative).mkdir(parents=True, exist_ok=True)


def prepare(component):
    existing = json.loads(LOCK.read_text()) if LOCK.exists() else {"components": {}}
    packages = existing.get("components", {}).get(component, {}).get("packages")
    if not packages:
        packages = resolve(component)
    TOOLS.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(TOOLS, 0o700)
    archives = TOOLS / "packages"
    archives.mkdir(exist_ok=True)
    PREFIX.mkdir(exist_ok=True)
    for package in packages:
        run(["apt-get", "download", f"{package['name']}={package['version']}"], cwd=archives)
        found = []
        for archive in archives.glob("*.deb"):
            metadata = run(["dpkg-deb", "-f", archive, "Package", "Version"]).splitlines()
            if metadata == [f"Package: {package['name']}", f"Version: {package['version']}"]:
                found.append(archive)
        if len(found) != 1:
            raise RuntimeError(
                "Cannot identify one exact downloaded package; "
                "no system installation was performed."
            )
        run(["dpkg-deb", "-x", found[0], PREFIX])
        package["archive"] = found[0].name
        package["copyright_path"] = f"root/usr/share/doc/{package['name']}/copyright"
        copyright = TOOLS / package["copyright_path"]
        if copyright.is_file():
            names = re.findall(
                r"/usr/share/common-licenses/([A-Za-z0-9_.+-]+)",
                copyright.read_text(errors="replace"),
            )
            for name in names:
                public_license = Path("/usr/share/common-licenses") / name.rstrip(".")
                if public_license.is_file():
                    destination = TOOLS / "licenses" / public_license.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(public_license, destination)
    if component == "office":
        relocate_office()
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {"schema": 1, "components": {}}
    lock["components"][component] = {"platform": platform.machine(), "packages": packages}
    temporary = LOCK.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(lock, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, LOCK)
    return {
        "component": component,
        "packages": len(packages),
        "download_bytes": sum(p["download_bytes"] for p in packages),
        "installed_bytes": sum(p["installed_bytes"] for p in packages),
        "prefix": str(PREFIX),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "prepare", "status"])
    parser.add_argument("component", choices=[*TARGETS, "asr"], nargs="?", default="ocr")
    args = parser.parse_args()
    if args.command == "status":
        sys.path.insert(0, str(ROOT / "apps/server/src"))
        from workbench.modules.files.advanced import capabilities

        result = capabilities()
    elif args.component == "asr":
        result = asr_plan() if args.command == "plan" else prepare_asr()
    elif args.command == "plan":
        packages = resolve(args.component)
        result = {
            "packages": packages,
            "download_bytes": sum(p["download_bytes"] for p in packages),
            "installed_bytes": sum(p["installed_bytes"] for p in packages),
        }
    else:
        result = prepare(args.component)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None
