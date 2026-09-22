"""Collections CRUD, reindex and progress."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/collections")

Globs = list[str]


class CollectionIn(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute folder path.")
    name: str = Field("", max_length=200)
    include: Globs = Field(default_factory=list, max_length=50)
    exclude: Globs | None = Field(None, max_length=50, description="Omit for the default exclusions.")
    watch: bool = False
    code: bool = False


class CollectionPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    include: Globs | None = Field(None, max_length=50)
    exclude: Globs | None = Field(None, max_length=50)
    enabled: bool | None = None
    watch: bool | None = None
    code: bool | None = None


def _with_progress(svc, collection) -> dict:
    data = collection.to_dict()
    data["progress"] = svc.worker.progress(collection.id)
    return data


@router.get("")
def list_collections(request: Request):
    svc = services(request)
    return {"collections": [_with_progress(svc, c) for c in svc.collections.list()]}


@router.post("", status_code=201)
def add_collection(request: Request, body: CollectionIn):
    svc = services(request)
    try:
        collection = svc.add_collection(body.name, body.path, body.include, body.exclude, body.watch, body.code)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _with_progress(svc, collection)


@router.get("/{collection_id}")
def get_collection(request: Request, collection_id: int):
    svc = services(request)
    collection = svc.collections.get(collection_id)
    if collection is None:
        raise HTTPException(404, "Collection not found.")
    return _with_progress(svc, collection)


@router.patch("/{collection_id}")
def patch_collection(request: Request, collection_id: int, body: CollectionPatch):
    svc = services(request)
    if svc.collections.get(collection_id) is None:
        raise HTTPException(404, "Collection not found.")
    collection = svc.update_collection(collection_id, body.model_dump())
    return _with_progress(svc, collection)


@router.delete("/{collection_id}")
def delete_collection(request: Request, collection_id: int):
    if not services(request).remove_collection(collection_id):
        raise HTTPException(404, "Collection not found.")
    return {"ok": True}


@router.post("/{collection_id}/reindex")
def reindex(request: Request, collection_id: int):
    svc = services(request)
    try:
        queued = svc.reindex(collection_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return {"ok": True, "queued": queued, "progress": svc.worker.progress(collection_id)}


@router.get("/{collection_id}/progress")
def progress(request: Request, collection_id: int):
    svc = services(request)
    if svc.collections.get(collection_id) is None:
        raise HTTPException(404, "Collection not found.")
    return {"progress": svc.worker.progress(collection_id)}
