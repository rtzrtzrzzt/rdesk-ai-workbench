#!/usr/bin/env python3
"""Real local-endpoint tool round trips and supplied-image checks."""

import argparse
import base64
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from local_http import open_local
from modelctl import CONFIG, DATA, ROOT, health, now, write_json, write_private


def post(payload, directory, name):
    write_json(directory / f"{name}-request.json", payload)
    request = urllib.request.Request(
        f"http://{CONFIG['host']}:{CONFIG['port']}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with open_local(request, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        write_private(directory / f"{name}-error.txt", error.read())
        raise
    write_json(directory / f"{name}-response.json", result)
    return result


def payload(messages):
    return {
        "model": CONFIG["served_model_name"],
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def verify_tools(directory):
    note = directory / "actual-tool-note.md"
    note.write_text("# 当前模型的真实工具调用记录\n", encoding="utf-8")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_deployment",
                "description": "读取当前真实部署配置，取得固定模型修订。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "append_validation_note",
                "description": "将刚读取的模型修订追加到本次受控验收记录文件。",
                "parameters": {
                    "type": "object",
                    "properties": {"model_revision": {"type": "string"}},
                    "required": ["model_revision"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    messages = [
        {
            "role": "user",
            "content": (
                "请先调用read_deployment读取当前真实部署的固定模型修订，"
                "再调用append_validation_note把这个修订写入本次验收记录。"
                "两个工具都实际完成后，简短报告修订与写入结果。"
            ),
        }
    ]
    read_done, edit_done, final, usage = False, False, "", []
    for turn in range(5):
        req = payload(messages)
        req["tools"] = tools
        body = post(req, directory, f"turn-{turn}")
        usage.append(body.get("usage"))
        message = body["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            final = message.get("content") or ""
            break
        assistant = {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
        if message.get("reasoning_content"):
            assistant["reasoning_content"] = message["reasoning_content"]
        messages.append(assistant)
        for call in calls:
            function = call["function"]
            args = json.loads(function["arguments"])
            if function["name"] == "read_deployment" and args == {}:
                result = json.loads((ROOT / "runtime/model/deployment.json").read_text())
                read_done = True
            elif function["name"] == "append_validation_note" and set(args) == {"model_revision"}:
                revision = args["model_revision"]
                if not read_done or revision != CONFIG["model_revision"]:
                    result = {
                        "error": "Must read the actual deployment first and use its exact revision"
                    }
                else:
                    # Fixed target and bounded content: model cannot select a host path.
                    with note.open("a", encoding="utf-8") as handle:
                        handle.write(f"固定模型修订：{revision}\n")
                    result = {
                        "written": True,
                        "model_revision": revision,
                        "artifact": str(note.relative_to(ROOT)),
                        "bytes": note.stat().st_size,
                    }
                    edit_done = True
            else:
                raise ValueError(f"Unexpected tool or parameters: {function['name']}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            write_json(directory / f"turn-{turn}-tool-results.json", messages)
    actual = note.read_text()
    return {
        "read_only_tool": read_done,
        "controlled_file_edit": edit_done,
        "file_contains_actual_revision": CONFIG["model_revision"] in actual,
        "final_content": final,
        "usage_by_turn": usage,
        "passed": bool(read_done and edit_done and CONFIG["model_revision"] in actual and final),
    }


def verify_image(directory, source):
    source = source.resolve(strict=True)
    data = source.read_bytes()
    if len(data) > 5 * 1024**2:
        raise ValueError("S0 image must be at most 5 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    else:
        raise ValueError("Supply an actual PNG/JPEG screenshot")
    write_json(
        directory / "image-provenance.json",
        {"source": str(source), "bytes": len(data), "mtime_ns": source.stat().st_mtime_ns},
    )
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "请阅读这张真实工作台截图，只回答：页面标题、中央模型服务卡片的模型名称、"
                        "卡片右侧按钮文字，以及该模型当前显示的验证状态。"
                        "请按截图回答，不推测服务已通过验证。"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()},
                },
            ],
        }
    ]
    result = post(payload(messages), directory, "image")
    content = result["choices"][0]["message"].get("content") or ""
    # These visible labels were inspected in the preserved real screenshot.
    expected = ["模型与连接", "Flash-Next", "检查连接", "未验证"]
    return {
        "content": content,
        "usage": result.get("usage"),
        "visible_labels": {label: label in content for label in expected},
        "passed": all(label in content for label in expected),
        "scope": "supplied screenshot reading; does not verify screen-coordinate grounding",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capability", choices=["tools", "image"])
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    if not health():
        raise RuntimeError("Engine endpoint is not ready")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = DATA / "evidence" / (stamp + "-" + args.capability)
    directory.mkdir(mode=0o700, parents=True)
    if args.capability == "image" and not args.image:
        parser.error("image requires --image with the preserved connections screenshot")
    result = (
        verify_tools(directory)
        if args.capability == "tools"
        else verify_image(directory, args.image)
    )
    result.update(checked_at=now(), model_revision=CONFIG["model_revision"])
    write_json(directory / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
