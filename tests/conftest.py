import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from borges.config import Config  # noqa: E402
from borges.main import create_app  # noqa: E402
from borges.services import Services  # noqa: E402
from fixtures import make_library  # noqa: E402


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(data_dir=tmp_path / "data", embed_backend="fake", watch=False, autostart=False, data_dir_configured=True)
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def library(tmp_path) -> Path:
    folder = tmp_path / "library"
    make_library(folder)
    return folder


@pytest.fixture
def services(tmp_path):
    svc = Services(make_config(tmp_path))
    svc.worker.start()
    yield svc
    svc.stop()


@pytest.fixture
def indexed(services, library):
    """Services with the fixture library indexed (fake embedder)."""
    collection = services.add_collection("Pruebas", str(library), [], None, False, False)
    assert services.worker.wait_idle(60)
    return services, collection


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(make_config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.services = app.state.services
        yield test_client
