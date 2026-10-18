"""Local browser session activity and status contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from workbench.contracts.base import PublicResponse


class AccountActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountSecurity(PublicResponse):
    idle_timeout_seconds: int
    session_expires_at: datetime
    last_active_at: datetime
