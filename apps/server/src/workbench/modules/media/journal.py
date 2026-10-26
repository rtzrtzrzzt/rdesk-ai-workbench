"""Private atomic receipts, including bounded base64 image responses."""

import json

from workbench.infrastructure.vault.private import read_private, write_json


def load(path):
    result = json.loads(read_private(path, 128 * 1024**2))
    if not isinstance(result, dict):
        raise ValueError("Media journal must be an object")
    return result


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(path, value, exclusive=False)
