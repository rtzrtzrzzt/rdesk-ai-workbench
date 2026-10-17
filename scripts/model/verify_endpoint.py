#!/usr/bin/env python3
"""Explicit real inference checks. Saves raw responses; never marks unknown modalities verified."""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from local_http import open_local
from modelctl import CONFIG, DATA, health, now, write_json, write_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--prompt", default="请用一句简短中文说明你可以协助什么。")
    args = parser.parse_args()
    if not health():
        raise RuntimeError("Engine endpoint is not ready")
    payload = {
        "model": CONFIG["served_model_name"],
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": 256,
        "temperature": 0,
        "stream": args.stream,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if args.stream:
        payload["stream_options"] = {"include_usage": True}
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = DATA / "evidence" / (stamp + ("-stream" if args.stream else "-text"))
    directory.mkdir(mode=0o700, parents=True)
    write_json(directory / "request.json", payload)
    req = urllib.request.Request(
        f"http://{CONFIG['host']}:{CONFIG['port']}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started, first = time.monotonic(), None
    usage, finish, content, done = None, None, "", False
    with open_local(req, timeout=300) as response:
        if args.stream:
            raw = []
            for line in response:
                text = line.decode()
                raw.append(text)
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                usage = event.get("usage") or usage
                for choice in event.get("choices", []):
                    token = choice.get("delta", {}).get("content") or ""
                    if token and first is None:
                        first = time.monotonic() - started
                    content += token
                    finish = choice.get("finish_reason") or finish
            write_private(directory / "response.sse", "".join(raw).encode())
        else:
            body = json.load(response)
            write_json(directory / "response.json", body)
            usage = body.get("usage")
            choice = body.get("choices", [{}])[0]
            finish = choice.get("finish_reason")
            content = choice.get("message", {}).get("content") or ""
    result = {
        "checked_at": now(),
        "model_revision": CONFIG["model_revision"],
        "stream": args.stream,
        "content": content,
        "finish_reason": finish,
        "usage": usage,
        "first_content_seconds": first,
        "elapsed_seconds": time.monotonic() - started,
        "sse_done": done if args.stream else None,
        "passed": bool(content and finish and usage and (done or not args.stream)),
    }
    write_json(directory / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
