"""Operator-requested import sources; no install hooks, filters or implicit execution."""

import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from workbench.contracts.errors import AppError
from workbench.modules.extensions import storage
from workbench.modules.extensions.dto import ExtensionSource


@contextmanager
def prepare(source: ExtensionSource):
    with storage.staging() as temporary:
        unpacked = temporary / "source"
        unpacked.mkdir(mode=0o700)
        revision = None
        if source.kind == "directory":
            storage.copy_directory(Path(source.location).expanduser(), unpacked)
        elif source.kind == "zip":
            storage.unpack_zip(Path(source.location).expanduser(), unpacked)
            # Conventional release ZIPs often wrap the package in one directory.
            children = list(unpacked.iterdir())
            if len(children) == 1 and children[0].is_dir():
                unpacked = children[0]
        else:
            url = urlsplit(source.location)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.fragment
                or url.query
                or url.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise AppError("extension_git_url", "Git来源须为不含凭据的HTTPS仓库地址。", 422)
            reference = source.revision or "HEAD"
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,199}", reference):
                raise AppError("extension_git_revision", "Git版本格式无效。", 422)
            repository = temporary / "git"
            env = {
                key: os.environ[key]
                for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG")
                if key in os.environ
            }
            env.update(
                GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_TERMINAL_PROMPT="0",
                GIT_ASKPASS="",
                GIT_LFS_SKIP_SMUDGE="1",
            )
            base = [
                "git",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "http.followRedirects=false",
            ]

            def git(*args, capture=False):
                try:
                    return subprocess.run(
                        base + list(args),
                        env=env,
                        cwd=temporary,
                        check=True,
                        timeout=60,
                        capture_output=True,
                    ).stdout
                except (subprocess.SubprocessError, OSError):
                    raise AppError(
                        "extension_git_failed", "仓库或指定版本无法读取；未执行安装脚本。", 422
                    ) from None

            git("init", "--bare", str(repository))
            git(
                "-C",
                str(repository),
                "fetch",
                "--depth=1",
                "--no-recurse-submodules",
                source.location,
                reference,
            )
            revision = git("-C", str(repository), "rev-parse", "FETCH_HEAD").decode().strip()
            # git archive skips checkout filters, hooks, LFS and submodule execution.
            archive = temporary / "source.zip"
            git(
                "-C",
                str(repository),
                "archive",
                "--format=zip",
                "--output=" + str(archive),
                revision,
            )
            storage.unpack_zip(archive, unpacked)
        selected = storage.contained(unpacked, source.subdirectory)
        if not selected.is_dir():
            raise AppError("extension_source_missing", "扩展子目录不存在。", 422)
        yield selected, revision
