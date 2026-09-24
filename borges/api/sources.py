"""Sources beyond folders: Faustus (the user's own past conversations). CRUD, sync, status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import services

router = APIRouter(prefix="/api/sources")


class FaustusSourceIn(BaseModel):
    base_url: str = Field(..., min_length=1, max_length=500, description="e.g. http://127.0.0.1:7000")
    name: str = Field("", max_length=200)
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=500)
    token: str = Field("", max_length=500, description="A Faustus API token (scope `sessions`); preferred over username/password.")
    include_projects: list[str] | None = Field(None, max_length=100, description="Faustus folder names to restrict to; omit for all.")
    poll_minutes: int = Field(10, ge=1, le=1440)
    enabled: bool = True


class FaustusSourcePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    base_url: str | None = Field(None, min_length=1, max_length=500)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=500)
    token: str | None = Field(None, max_length=500)
    include_projects: list[str] | None = Field(None, max_length=100)
    poll_minutes: int | None = Field(None, ge=1, le=1440)
    enabled: bool | None = None


def _with_progress(svc, collection) -> dict:
    data = collection.to_dict()
    data["progress"] = svc.worker.progress(collection.id)
    return data


def _require_faustus(svc, source_id: int):
    collection = svc.collections.get(source_id)
    if collection is None or collection.kind != "faustus":
        raise HTTPException(404, "Source not found.")
    return collection


@router.get("")
def list_sources(request: Request):
    svc = services(request)
    return {"sources": [_with_progress(svc, c) for c in svc.collections.list_by_kind("faustus")]}


@router.post("", status_code=201)
def add_source(request: Request, body: FaustusSourceIn):
    svc = services(request)
    if not body.token and not (body.username and body.password):
        raise HTTPException(400, "Provide either a token or a username and password.")
    config = {
        "base_url": body.base_url.strip(), "username": body.username, "password": body.password, "token": body.token,
        "include_projects": body.include_projects, "poll_minutes": body.poll_minutes,
    }
    collection = svc.add_faustus_source(body.name, config, body.enabled)
    return _with_progress(svc, collection)


@router.get("/{source_id}")
def get_source(request: Request, source_id: int):
    svc = services(request)
    return _with_progress(svc, _require_faustus(svc, source_id))


@router.patch("/{source_id}")
def patch_source(request: Request, source_id: int, body: FaustusSourcePatch):
    svc = services(request)
    _require_faustus(svc, source_id)
    patch = body.model_dump(exclude={"name", "enabled"}, exclude_none=True)
    collection = svc.update_faustus_source(source_id, body.name, patch, body.enabled)
    return _with_progress(svc, collection)


@router.delete("/{source_id}")
def delete_source(request: Request, source_id: int):
    svc = services(request)
    _require_faustus(svc, source_id)
    svc.remove_collection(source_id)
    return {"ok": True}


@router.post("/{source_id}/sync")
def sync_source(request: Request, source_id: int):
    svc = services(request)
    _require_faustus(svc, source_id)
    queued = svc.sync_source(source_id)
    return {"ok": True, "queued": queued, "progress": svc.worker.progress(source_id)}


@router.get("/{source_id}/status")
def source_status(request: Request, source_id: int):
    svc = services(request)
    collection = _require_faustus(svc, source_id)
    return {"sync_status": collection.sync_status, "progress": svc.worker.progress(source_id)}


@router.get("/faustus/recent-chats")
def recent_chats(request: Request, limit: int = 10, project: str = ""):
    svc = services(request)
    return {"chats": svc.queries.recent_chats(min(max(limit, 1), 100), project.strip() or None)}
