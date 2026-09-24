"""Sources beyond folders: Faustus (the user's own past conversations) and Links Hoard (saved links). CRUD, sync, status."""

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


class LinksSourceIn(BaseModel):
    name: str = Field("", max_length=200)
    state: str = Field("all", pattern="^(all|unread|read|archived)$", description="Which links to index.")
    tag: str = Field("", max_length=40, description="Only links carrying this tag; empty for all.")
    base_url: str = Field("", max_length=500, description="Only without the hub: Links Hoard's own URL, e.g. http://127.0.0.1:8790")
    token: str = Field("", max_length=500, description="Only with base_url: Links Hoard's bearer token.")
    poll_minutes: int = Field(30, ge=1, le=1440)
    enabled: bool = True


class SourcePatch(BaseModel):
    """Fields of both kinds; only those given are applied (a Faustus source ignores `state`, a links one `username`)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    base_url: str | None = Field(None, max_length=500)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=500)
    token: str | None = Field(None, max_length=500)
    include_projects: list[str] | None = Field(None, max_length=100)
    state: str | None = Field(None, pattern="^(all|unread|read|archived)$")
    tag: str | None = Field(None, max_length=40)
    poll_minutes: int | None = Field(None, ge=1, le=1440)
    enabled: bool | None = None


FaustusSourcePatch = SourcePatch  # the old name, kept
SOURCE_KINDS = ("faustus", "links")


def _with_progress(svc, collection) -> dict:
    data = collection.to_dict()
    data["progress"] = svc.worker.progress(collection.id)
    return data


def _require_source(svc, source_id: int):
    collection = svc.collections.get(source_id)
    if collection is None or collection.kind not in SOURCE_KINDS:
        raise HTTPException(404, "Source not found.")
    return collection


_require_faustus = _require_source


@router.get("")
def list_sources(request: Request, kind: str = ""):
    svc = services(request)
    kinds = (kind,) if kind in SOURCE_KINDS else SOURCE_KINDS
    return {"sources": [_with_progress(svc, c) for c in svc.collections.list() if c.kind in kinds]}


@router.post("/links", status_code=201)
def add_links_source(request: Request, body: LinksSourceIn):
    """A Links Hoard source. Without `base_url` the sync goes through the Hoard Hub proxy (no token needed)."""
    svc = services(request)
    config = {"state": body.state, "tag": body.tag.strip(), "base_url": body.base_url.strip(), "token": body.token,
              "poll_minutes": body.poll_minutes}
    collection = svc.add_links_source(body.name, config, body.enabled)
    return _with_progress(svc, collection)


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
def patch_source(request: Request, source_id: int, body: SourcePatch):
    svc = services(request)
    _require_source(svc, source_id)
    patch = body.model_dump(exclude={"name", "enabled"}, exclude_none=True)
    collection = svc.update_source(source_id, body.name, patch, body.enabled)
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
