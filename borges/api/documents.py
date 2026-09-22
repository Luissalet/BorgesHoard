"""Documents: list, metadata + outline, and page/section text."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from .deps import services

router = APIRouter(prefix="/api/documents")


@router.get("")
def list_documents(
    request: Request,
    collection: int | None = Query(None, ge=1),
    q: str = Query("", max_length=200),
    status: str | None = Query(None, pattern="^(ok|error)$"),
    limit: int = Query(100, ge=1, le=500),
    cursor: int | None = Query(None, ge=1),
):
    return services(request).queries.list_documents(collection, q.strip(), limit, cursor, status)


@router.get("/{document_id}")
def get_document(request: Request, document_id: int):
    doc = services(request).queries.document(document_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    return doc


@router.get("/{document_id}/text")
def document_text(
    request: Request,
    document_id: int,
    page: int | None = Query(None, ge=1),
    section: int | None = Query(None, ge=1),
    unit: int | None = Query(None, ge=1),
):
    svc = services(request)
    if svc.queries.document_brief(document_id) is None:
        raise HTTPException(404, "Document not found.")
    result = svc.queries.unit(document_id, page=page, section=section, unit_id=unit)
    if result is None:
        raise HTTPException(404, "That page or section does not exist in this document.")
    return result
