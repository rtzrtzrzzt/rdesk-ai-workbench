#!/bin/sh
set -eu
workbench_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for workbench_python in "$workbench_root/.venv/bin/python" "$workbench_root/.tools/bootstrap/bin/python" python3.12 python3 python; do
    if command -v "$workbench_python" >/dev/null 2>&1 && "$workbench_python" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
        # File managers may launch an executable without a visible terminal.
        # Keep operational commands observable while CLI/check calls stay plain.
        if [ "${WORKBENCH_LAUNCHER_CONSOLE:-}" != 1 ] && [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
            case "${1:-start}" in
                start|stop)
                    if command -v gnome-terminal >/dev/null 2>&1; then
                        export WORKBENCH_LAUNCHER_CONSOLE=1
                        exec gnome-terminal --wait -- "$workbench_python" "$workbench_root/scripts/bootstrap.py" --pause "$@"
                    fi
                    ;;
            esac
        fi
        exec "$workbench_python" "$workbench_root/scripts/bootstrap.py" "$@"
    fi
done
echo "需要 Python 3.12：请从 https://www.python.org/downloads/ 安装本机官方版本。" >&2
exit 2
