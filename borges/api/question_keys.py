"""Question keys: setting, state and (re)generation. See `borges/question_keys.py`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/question-keys")


class SettingsPatch(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(None, max_length=500, description="OpenAI-compatible endpoint of the local model, e.g. http://127.0.0.1:11434/v1")
    model: str | None = Field(None, max_length=200)
    api_key: str | None = Field(None, max_length=500)


class GenerateBody(BaseModel):
    document_id: int | None = Field(None, ge=1)
    collection_id: int | None = Field(None, ge=1)
    force: bool = False
    max_chunks: int | None = Field(None, ge=1, le=5000)


def _check_scope(svc, document_id, collection_id) -> None:
    if document_id is not None and svc.queries.document_brief(document_id) is None:
        raise HTTPException(404, "Document not found.")
    if collection_id is not None and svc.collections.get(collection_id) is None:
        raise HTTPException(404, "Collection not found.")


@router.get("")
def state(request: Request, document: int | None = Query(None, ge=1), collection: int | None = Query(None, ge=1), probe: bool = True):
    svc = services(request)
    _check_scope(svc, document, collection)
    return svc.question_keys.status(document, collection, probe=probe)


@router.get("/settings")
def get_settings(request: Request):
    return services(request).question_keys.settings()


@router.put("/settings")
def put_settings(request: Request, body: SettingsPatch):
    return services(request).question_keys.update_settings(body.model_dump(exclude_none=True))


@router.post("/generate")
def generate(request: Request, body: GenerateBody):
    svc = services(request)
    _check_scope(svc, body.document_id, body.collection_id)
    return svc.question_keys.generate(body.document_id, body.collection_id, body.force, body.max_chunks)


@router.get("/chunk/{chunk_id}")
def chunk_questions(request: Request, chunk_id: int):
    svc = services(request)
    if svc.queries.chunk(chunk_id) is None:
        raise HTTPException(404, "Chunk not found.")
    return {"chunk_id": chunk_id, "questions": svc.question_keys.questions_for(chunk_id)}
