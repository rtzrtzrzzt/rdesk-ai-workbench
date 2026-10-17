"""Owner account commands and safe receipts; passwords remain write-only."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from workbench.contracts.base import PublicResponse


class AccountActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountSecurity(PublicResponse):
    idle_timeout_seconds: int
    session_expires_at: datetime
    last_active_at: datetime


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)
    request_id: UUID


class PasswordChangeReceipt(PublicResponse):
    request_id: UUID
    password_changed: Literal[True]
    current_session_retained: Literal[True]
    other_sessions_revoked: int
    changed_at: datetime


class AccountRecoveryReceipt(PublicResponse):
    request_id: UUID
    user_id: str
    password_changed: Literal[True]
    sessions_revoked: int
    recovered_at: datetime
