"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .hoard_link.appconfig import env_flag, env_int, env_str
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5184
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class Config:
    """Everything the process needs before the database exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    embed_backend: str = "auto"  # auto (fastembed) | fake | none
    model_name: str = DEFAULT_MODEL
    models_dir: Path | None = None  # defaults to <data_dir>/models
    embed_providers: tuple[str, ...] = ()  # e.g. ("CUDAExecutionProvider",) with the gpu extra
    watch: bool = True  # start watchdog observers for collections with watch=1
    autostart: bool = True  # start the worker and preload the model with the app
    allowed_hosts: tuple[str, ...] = ()  # extra Host values (exact or *.suffix) besides localhost
    data_dir_configured: bool = False
    defer_stale: bool = False  # merged shell requires an explicit automation choice

    @property
    def db_path(self) -> Path:
        return self.data_dir / "borges-hoard.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "mcp-token"

    @property
    def model_cache(self) -> Path:
        return self.models_dir or (self.data_dir / "models")

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = env_str("BORGES_DATA_DIR")
        port = env_int("BORGES_PORT", "PORT")
        if port is None or not 1 <= port <= 65535:
            port = DEFAULT_PORT
        models_raw = env_str("BORGES_MODELS_DIR")
        providers = tuple(p.strip() for p in (env_str("BORGES_EMBED_PROVIDERS") or "").split(",") if p.strip())
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=port,
            port_strict=env_flag("PORT_STRICT"),
            embed_backend=env_str("BORGES_EMBED", "auto") or "auto",
            model_name=env_str("BORGES_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            models_dir=Path(models_raw).expanduser() if models_raw else None,
            embed_providers=providers,
            watch=env_flag("BORGES_WATCH", True),
            autostart=env_flag("BORGES_AUTOSTART", True),
            allowed_hosts=parse_allowed_hosts(env_str("BORGES_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
        )
