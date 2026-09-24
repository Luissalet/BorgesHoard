"""Health and status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import __version__
from .deps import services
from ..hoard_link import family

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    return {"service": "borges-hoard", "version": __version__, "dataDirConfigured": request.app.state.config.data_dir_configured,
            "hoard_link": family.health_block()}


@router.get("/status")
def status(request: Request):
    return services(request).status()
