"""Human-readable lifecycle stages and progress extracted from the real model log."""

from __future__ import annotations

import re
import time
from pathlib import Path

from launcher_config import write_control


class Progress:
    def __init__(self, action, identifier, total):
        self.started = time.monotonic()
        self.identifier, self.action, self.total = identifier, action, total
        self.stage = 0
        self.last = ""
        self.last_printed = 0.0
        self.stages = []

    def begin(self, name):
        self.stage += 1
        self.name = name
        self.stages.append({"name": name, "status": "running"})
        self.update("正在处理", force=True)

    def update(self, detail, *, done=False, force=False):
        elapsed = int(time.monotonic() - self.started)
        if not force and detail == self.last and time.monotonic() - self.last_printed < 10:
            return
        if done:
            self.stages[-1]["status"] = "ready" if self.action == "start" else "stopped"
        self.stages[-1]["detail"] = detail
        self.last, self.last_printed = detail, time.monotonic()
        print(
            f"[{self.stage}/{self.total}] {self.name} · {detail}  "
            f"（已用时 {elapsed // 60}分{elapsed % 60:02d}秒）",
            flush=True,
        )
        self.publish("running")

    def publish(self, status, error=None):
        write_control(
            "state.json",
            {
                "version": 1,
                "id": self.identifier,
                "action": self.action,
                "status": status,
                "stages": self.stages,
                "elapsed_seconds": round(time.monotonic() - self.started, 1),
                "error": error,
            },
        )


class ModelLogProgress:
    def __init__(self):
        self.path = None
        self.position = 0
        self.pending = ""
        self.loads = 0
        self.detail = "检查环境、准备模型加载"

    def read(self, directory):
        if not directory:
            return self.detail
        path = Path(directory) / "server.log"
        if path != self.path:
            self.__init__()
            self.path = path
        try:
            with path.open("rb") as stream:
                stream.seek(self.position)
                chunk = stream.read(1024 * 1024)
                self.position = stream.tell()
        except OSError:
            return self.detail
        text = self.pending + chunk.decode("utf-8", "replace")
        pieces = re.split(r"[\r\n]", text)
        self.pending = pieces.pop()[-8192:]
        for line in pieces:
            if "Load weight begin" in line:
                self.loads += 1
                self.detail = "加载MTP权重" if self.loads > 1 else "加载主模型权重"
            match = re.search(r"Multi-thread loading shards:.*?\|\s*(\d+)\s*/\s*(\d+)", line)
            if match:
                label = "MTP权重" if self.loads > 1 else "主模型权重"
                self.detail = f"{label}分片 {match[1]}/{match[2]}"
            if "Load weight end" in line:
                self.detail = (
                    "准备MTP权重"
                    if "Qwen4ExpForConditionalGeneration" in line
                    else "权重已加载，初始化缓存与计算图"
                )
        current = re.search(r"Multi-thread loading shards:.*?\|\s*(\d+)\s*/\s*(\d+)", self.pending)
        if current:
            label = "MTP权重" if self.loads > 1 else "主模型权重"
            self.detail = f"{label}分片 {current[1]}/{current[2]}"
        return self.detail
