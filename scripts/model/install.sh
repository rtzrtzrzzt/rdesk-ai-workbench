#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
mkdir -p .local/model/evidence
python3 -m venv .runtime/model-env
REQUIREMENTS="runtime/model/requirements.in"
if [[ -f runtime/model/requirements.lock ]]; then
  REQUIREMENTS="runtime/model/requirements.lock"
fi
# --isolated deliberately ignores machine/user mirror configuration.
.runtime/model-env/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple \
  --constraint runtime/model/constraints.txt \
  --requirement "$REQUIREMENTS" \
  --report .local/model/evidence/installed-packages.json
.runtime/model-env/bin/python -m pip check
.runtime/model-env/bin/python -m pip inspect --local > .local/model/evidence/environment-packages.json
.runtime/model-env/bin/python -m pip freeze > runtime/model/requirements.lock
.runtime/model-env/bin/python scripts/model/check_environment.py
