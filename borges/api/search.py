"""Search and similar passages."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from .deps import services

router = APIRouter(prefix="/api")


@router.get("/search")
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    collection: int | None = Query(None, ge=1),
    mode: str = Query("hybrid", pattern="^(hybrid|bm25|dense)$"),
    limit: int = Query(10, ge=1, le=50),
    source: str | None = Query(None, pattern="^(folder|faustus)$", description="Restrict to one source kind, e.g. kind:faustus."),
    since: str | None = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$", description="ISO date; only chats/documents dated on or after this."),
    until: str | None = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$", description="ISO date; only chats/documents dated on or before this."),
):
    svc = services(request)
    if collection is not None and svc.collections.get(collection) is None:
        raise HTTPException(404, "Collection not found.")
    try:
        return svc.search.search(q, mode, limit, collection, source=source, since=since, until=until)
    except ValueError as error:  # query too short
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:  # model failed to load mid-request
        raise HTTPException(503, str(error)) from error


@router.get("/similar/{chunk_id}")
def similar(request: Request, chunk_id: int, limit: int = Query(10, ge=1, le=50)):
    svc = services(request)
    try:
        return {"chunk_id": chunk_id, "hits": svc.search.similar(chunk_id, limit)}
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/chunks/{chunk_id}")
def chunk(request: Request, chunk_id: int):
    row = services(request).queries.chunk(chunk_id)
    if row is None:
        raise HTTPException(404, "Chunk not found.")
    return row
