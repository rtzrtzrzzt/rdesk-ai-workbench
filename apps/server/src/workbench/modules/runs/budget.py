"""Per-generation limits are independent from the model's context capacity."""

import json
import math
from dataclasses import dataclass
from datetime import datetime

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now


@dataclass(frozen=True)
class GenerationBudget:
    output_tokens: int
    remaining_seconds: float | None
    finishing: bool
    remaining_rounds: int
    working_seconds: float | None
    recovering: bool = False

    def timeout(self, configured: float) -> float:
        if self.remaining_seconds is None:
            return configured
        return min(configured, max(0.1, self.remaining_seconds))

    def notice(self, run_id: str | None = None) -> dict:
        facts = {
            "本轮最大输出token（含思考）": self.output_tokens,
            "剩余模型轮数": self.remaining_rounds,
        }
        if self.remaining_seconds is not None:
            facts["剩余任务秒数"] = math.ceil(self.remaining_seconds)
        if self.recovering:
            facts["输出上限后的有限恢复"] = "本任务仅一次；不延长硬截止或总调用预算"
        instructions = (
            "已进入截止前收尾阶段，本轮不再提供工具。请根据实际回执说明已修改内容、"
            "必要检查及其结果、未验证项或阻塞；没有完成的工作须明确说明，不能宣称通过。"
            if self.finishing
            else "先用工具取得完成任务所需的事实，及时实施与验证，避免在思考中反复穷举。"
            "为本任务需要的检查和交付留出时间；单轮输出限制不等于模型上下文长度。"
        )
        if self.recovering:
            instructions = (
                "此前生成达到单轮输出上限，未完整提交的工具调用没有执行。"
                "不要重复长篇分析，基于已有真实工具回执完成最小必要实施与检查；"
                "仍无法完成时明确说明，不得假报成功。"
            ) + instructions
        return {
            "role": "user",
            "source_kind": "runtime_budget",
            "_run_id": run_id,
            "content": "<tool_response>\n工作台当前执行预算（不增加权限）：\n"
            + json.dumps(facts, ensure_ascii=False)
            + "\n"
            + instructions
            + "\n</tool_response>",
        }


def generation_budget(run, round_number: int = 0, *, observed_at: datetime | None = None):
    options = run.config_snapshot.get("runtime_options", {})
    limit = options.get("model_output_tokens", 8192)
    recovery = run.config_snapshot.get("safety", {}).get("output_recovery", {})
    recovering = recovery.get("round_number") == round_number
    if recovering:
        limit = recovery["tokens"]
    remaining = None
    finishing = False
    working_seconds = None
    if run.deadline_at is not None:
        remaining = (run.deadline_at - (observed_at or now())).total_seconds()
        if remaining <= 0:
            raise AppError("deadline_exceeded", "任务截止时间已到。", 409, "budget")
        # A short task must still have an execution phase. The reserve is at
        # most one fifth of its original allowance, and never extends it.
        total = max(0, (run.deadline_at - run.created_at).total_seconds())
        reserve = min(options.get("finish_reserve_seconds", 60), total / 5)
        finishing = reserve > 0 and remaining <= reserve
        if reserve > 0 and not finishing:
            working_seconds = remaining - reserve
        if finishing:
            limit = min(limit, 2048)
    return GenerationBudget(
        output_tokens=limit,
        remaining_seconds=remaining,
        finishing=finishing,
        remaining_rounds=max(0, options.get("max_model_rounds", 65536) - round_number),
        working_seconds=working_seconds,
        recovering=recovering,
    )
