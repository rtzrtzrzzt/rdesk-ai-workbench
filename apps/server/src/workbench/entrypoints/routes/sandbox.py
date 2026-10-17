"""Actual optional isolation capabilities; policy selection belongs to Run defaults."""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import USER
from workbench.modules.sandbox import SandboxCapability, provider_for

router = APIRouter(prefix="/api/v1/settings/sandbox", responses=ERROR_RESPONSES)


@router.get("", response_model=list[SandboxCapability])
async def capabilities(user: USER):
    def inspect():
        return [
            asdict(provider_for(name).capability()) for name in ("native_scoped", "linux_bwrap")
        ]

    return await asyncio.to_thread(inspect)
