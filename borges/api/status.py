"""Status. `/api/health` is the shared `hoard_link.service.health_router`, mounted in main.py."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import services

router = APIRouter(prefix="/api")


@router.get("/status")
def status(request: Request):
    return services(request).status()
