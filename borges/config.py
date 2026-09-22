"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5184
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


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
    data_dir_configured: bool = False

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
        raw_dir = _env("BORGES_DATA_DIR")
        port_raw = _env("BORGES_PORT") or _env("PORT") or str(DEFAULT_PORT)
        try:
            port = int(port_raw)
        except ValueError:
            port = DEFAULT_PORT
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        models_raw = _env("BORGES_MODELS_DIR")
        providers = tuple(p.strip() for p in _env("BORGES_EMBED_PROVIDERS").split(",") if p.strip())
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=port,
            port_strict=_env("PORT_STRICT") == "1",
            embed_backend=_env("BORGES_EMBED", "auto") or "auto",
            model_name=_env("BORGES_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            models_dir=Path(models_raw).expanduser() if models_raw else None,
            embed_providers=providers,
            watch=_env("BORGES_WATCH", "1") != "0",
            autostart=_env("BORGES_AUTOSTART", "1") != "0",
            data_dir_configured=bool(raw_dir),
        )
