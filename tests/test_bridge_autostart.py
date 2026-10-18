"""Discovery remains inert even when a retired autostart variable is present."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(monkeypatch, autostart):
    monkeypatch.setenv('BORGES_URL', 'http://127.0.0.1:1')
    monkeypatch.setenv('BORGES_BRIDGE_AUTOSTART', autostart)
    spec = importlib.util.spec_from_file_location('borges_bridge_under_test', ROOT / 'mcp_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discovery_is_static_with_autostart_off(monkeypatch, tmp_path):
    monkeypatch.setenv('BORGES_DATA_DIR', str(tmp_path / 'not-created'))
    module = load(monkeypatch, '0')
    assert len(module.catalog()) == 378
    assert not (tmp_path / 'not-created').exists()


def test_retired_autostart_flag_never_spawns_an_app(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('catalog discovery must not spawn')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    module = load(monkeypatch, '1')
    assert 'library_search' in {t['name'] for t in module.catalog()}
    assert not hasattr(module, 'build')


def test_fresh_discovery_imports_no_native_engine(monkeypatch):
    script = "import sys,mcp_server; assert len(mcp_server.catalog())==378; assert not any(k in sys.modules for k in ['borges.main','kafka_hoard.main','fusion.links_engine','fastembed'])"
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
