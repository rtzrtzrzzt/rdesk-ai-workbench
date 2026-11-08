"""Loopback owner controls for the separate optional public listener."""

from fastapi import APIRouter
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import USER
from workbench.modules.access import listener
from workbench.modules.agent_services.dto import RequestCommand

router = APIRouter(prefix="/api/v1/agent-api/listener", responses=ERROR_RESPONSES)


@router.get("", response_model=listener.ListenerView)
def status(user: USER):
    return listener.view()


@router.post("", response_model=listener.ListenerView)
def configure(command: listener.ListenerSave, user: USER):
    return listener.save(command)


@router.post("/start", response_model=listener.ListenerView)
def start(command: RequestCommand, user: USER):
    return listener.start()


@router.post("/stop", response_model=listener.ListenerView)
def stop(command: RequestCommand, user: USER):
    return listener.stop()
