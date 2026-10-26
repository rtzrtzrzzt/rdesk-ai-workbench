"""Actual entrypoint import graph; no database mutations or service operations."""

import subprocess
import sys
from pathlib import Path


def test_fresh_cli_process_resolves_command_foreign_keys():
    source = Path(__file__).resolve().parents[1] / "apps/server/src"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "from workbench.entrypoints import cli;"
            "from workbench.modules.processes.domain import CommandExecution;"
            "[key.column for key in CommandExecution.__table__.foreign_keys]",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
