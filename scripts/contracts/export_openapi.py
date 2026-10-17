#!/usr/bin/env python3
"""Export the static API contract without starting services or authenticating."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "apps/web/openapi.json")
    args = parser.parse_args()
    sys.path.insert(0, str(PROJECT_ROOT / "apps/server/src"))
    from workbench.entrypoints.api import app

    schema = app.openapi()
    destination = args.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Exported OpenAPI {schema['openapi']} to {destination}")


if __name__ == "__main__":
    main()
