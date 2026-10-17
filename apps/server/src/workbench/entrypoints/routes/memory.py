from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.memory import dto, service

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", responses=ERROR_RESPONSES)


def _writer(db, user) -> str:
    # Memory publishing owns its journal transactions across a filesystem write.
    # Release the authentication read transaction before that independent work.
    user_id = user.id
    db.rollback()
    return user_id


@router.get("/memory/documents", response_model=dto.DocumentList)
def documents(workspace_id: UUID, db: DB, user: USER, scope: dto.Scope = "workspace"):
    return service.list_documents(db, user.id, str(workspace_id), scope)


@router.post("/memory/documents", response_model=dto.Document, status_code=201)
def create(workspace_id: UUID, command: dto.MemoryCreate, db: DB, user: USER):
    return service.create_document(db, _writer(db, user), str(workspace_id), command)


@router.get("/memory/documents/{document_id}", response_model=dto.Document)
def read(workspace_id: UUID, document_id: UUID, db: DB, user: USER):
    return service.read_document(db, user.id, str(workspace_id), str(document_id))


@router.get("/memory/documents/{document_id}/revisions", response_model=dto.MemoryRevisionList)
def revisions(workspace_id: UUID, document_id: UUID, db: DB, user: USER):
    return service.list_revisions(db, user.id, str(workspace_id), str(document_id))


@router.get(
    "/memory/documents/{document_id}/revisions/{version}", response_model=dto.MemoryRevisionView
)
def revision(workspace_id: UUID, document_id: UUID, version: str, db: DB, user: USER):
    return service.read_revision(db, user.id, str(workspace_id), str(document_id), version)


@router.get(
    "/memory/documents/{document_id}/revisions/{version}/compare",
    response_model=dto.MemoryRevisionComparison,
)
def compare(workspace_id: UUID, document_id: UUID, version: str, db: DB, user: USER):
    return service.compare_revision(db, user.id, str(workspace_id), str(document_id), version)


@router.post("/memory/documents/{document_id}/restore", response_model=dto.Document)
def restore(workspace_id: UUID, document_id: UUID, command: dto.MemoryRestore, db: DB, user: USER):
    return service.restore_document(
        db, _writer(db, user), str(workspace_id), str(document_id), command
    )


@router.post("/memory/documents/{document_id}", response_model=dto.Document)
def update(workspace_id: UUID, document_id: UUID, command: dto.MemoryUpdate, db: DB, user: USER):
    return service.update_document(
        db, _writer(db, user), str(workspace_id), str(document_id), command
    )


@router.post("/memory/documents/{document_id}/forget", response_model=dto.ForgetResult)
def forget(workspace_id: UUID, document_id: UUID, command: dto.MemoryForget, db: DB, user: USER):
    return service.forget_document(
        db, _writer(db, user), str(workspace_id), str(document_id), command
    )


@router.get("/memory/search", response_model=dto.SearchResult)
def search(
    workspace_id: UUID,
    db: DB,
    user: USER,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    scope: dto.Scope = "workspace",
):
    return service.search(db, user.id, str(workspace_id), q, scope)


@router.get("/rules", response_model=dto.RulesSnapshot)
def rules(
    workspace_id: UUID,
    db: DB,
    user: USER,
    paths: Annotated[list[str] | None, Query(max_length=32)] = None,
):
    return service.load_rules(db, user.id, str(workspace_id), paths or [])


@router.post("/rules/global", response_model=dto.GlobalRules)
def global_rules(workspace_id: UUID, command: dto.GlobalRulesUpdate, db: DB, user: USER):
    return service.update_global_rules(db, _writer(db, user), str(workspace_id), command)


@router.get("/memory/proposals", response_model=list[dto.Proposal])
def proposals(workspace_id: UUID, db: DB, user: USER, scope: dto.Scope = "workspace"):
    return service.list_proposals(db, user.id, str(workspace_id), scope)


@router.post("/memory/proposals", response_model=dto.Proposal, status_code=201)
def propose(workspace_id: UUID, command: dto.ProposalCreate, db: DB, user: USER):
    return service.create_proposal(db, _writer(db, user), str(workspace_id), command)


@router.post("/memory/proposals/{proposal_id}/decisions", response_model=dto.Proposal)
def decide(
    workspace_id: UUID, proposal_id: UUID, command: dto.ProposalDecision, db: DB, user: USER
):
    return service.decide_proposal(
        db, _writer(db, user), str(workspace_id), str(proposal_id), command
    )
