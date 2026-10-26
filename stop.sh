#!/bin/sh
set -eu
workbench_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$workbench_root/start.sh" stop "$@"
