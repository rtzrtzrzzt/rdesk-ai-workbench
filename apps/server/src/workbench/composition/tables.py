"""Import each module's table declarations for migrations, without runtime side effects."""

from workbench.modules.automation.domain import (
    AutomationAction,
    AutomationPolicy,
    AutomationSession,
    AutomationSettings,
    DesktopWriterLease,
)
from workbench.modules.collaboration.domain import AgentMessage, AgentSlot, AgentTurn
from workbench.modules.context.domain import ThreadContext
from workbench.modules.conversations import ConversationDeletion, Message, QueuedInput, Thread
from workbench.modules.extensions.domain import (
    ExtensionInstallation,
    ExtensionMCPBinding,
    ExtensionOverride,
    ExtensionRevision,
)
from workbench.modules.files.domain import Artifact, Upload
from workbench.modules.identity import AccountCommandReceipt, AuthSession, LoginThrottle, User
from workbench.modules.interactions import Approval, Question
from workbench.modules.mcp.host_domain import MCPConnection, MCPSnapshot, ToolExchange
from workbench.modules.media.domain import MediaConnection, MediaResidency, MediaTask
from workbench.modules.memory.domain import (
    MemoryDocument,
    MemoryProposal,
    MemoryRevision,
    MemoryScope,
    MemoryTombstone,
    MemoryWriteJournal,
)
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.preferences.configuration import ConfigurationState
from workbench.modules.preferences.domain import RuntimeDefaults, WorkspaceRuntimeDefaults
from workbench.modules.processes.domain import CommandExecution
from workbench.modules.runs.domain import (
    Attempt,
    ClientRequest,
    Job,
    Run,
    RunEvent,
    Step,
    WorkerHeartbeat,
)
from workbench.modules.search.domain import SearchConnection
from workbench.modules.tools.domain import Operation
from workbench.modules.usage import UsageRecord
from workbench.modules.workspaces import Workspace

__all__ = [
    "ApiClient",
    "ApiKey",
    "Grant",
    "ExecutionBinding",
    "ExternalNamespace",
    "AgentService",
    "AgentServiceRevision",
    "AgentSessionBinding",
    "ChangeRecord",
    "CommandCapture",
    "MediaConnection",
    "MediaTask",
    "MediaResidency",
    "ExtensionInstallation",
    "ExtensionRevision",
    "ExtensionOverride",
    "ExtensionMCPBinding",
    "AutomationAction",
    "AutomationPolicy",
    "AutomationSession",
    "AutomationSettings",
    "DesktopWriterLease",
    "User",
    "MCPConnection",
    "MCPSnapshot",
    "ToolExchange",
    "RuntimeDefaults",
    "WorkspaceRuntimeDefaults",
    "ConfigurationState",
    "CommandExecution",
    "AgentSlot",
    "AgentTurn",
    "AgentMessage",
    "AuthSession",
    "AccountCommandReceipt",
    "LoginThrottle",
    "Workspace",
    "ModelConnection",
    "ModelRevision",
    "Thread",
    "ThreadContext",
    "QueuedInput",
    "ConversationDeletion",
    "Message",
    "Run",
    "Job",
    "RunEvent",
    "ClientRequest",
    "Step",
    "Attempt",
    "WorkerHeartbeat",
    "Question",
    "Approval",
    "Operation",
    "UsageRecord",
    "MemoryDocument",
    "MemoryProposal",
    "MemoryRevision",
    "MemoryScope",
    "MemoryTombstone",
    "MemoryWriteJournal",
    "Artifact",
    "Upload",
    "SearchConnection",
]
from workbench.modules.access.domain import (
    ApiClient,
    ApiKey,
    ExecutionBinding,
    ExternalNamespace,
    Grant,
)
from workbench.modules.agent_services.domain import (
    AgentService,
    AgentServiceRevision,
    AgentSessionBinding,
)
from workbench.modules.changes.domain import ChangeRecord, CommandCapture
