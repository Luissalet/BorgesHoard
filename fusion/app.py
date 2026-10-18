import os
import asyncio
from pathlib import Path
from .native import SPECS, factory
from .runtime import create_runtime


def create_app(data_dir=None, port=None):
    root = Path(__file__).resolve().parent
    port = port or int(os.environ.get('BORGES_PORT', '5184'))
    data_dir = Path(data_dir or os.environ.get('BORGES_DATA_DIR') or root.parent / 'data')
    holder = {}
    async def dispatch(key, tool, args):
        module = holder['app'].state.modules[key]
        token = (module.data_dir / 'mcp-token').read_text(encoding='utf-8').strip() if module.app is not None else ''
        response = await module.request('POST', '/api/agent/call', json={'name': tool, 'arguments': args},
            headers={'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + token})
        response.raise_for_status()
        return response.json()
    def native_call(key, tool, args):
        return asyncio.run_coroutine_threadsafe(dispatch(key, tool, args), holder['app'].state.loop).result()
    def native_available(key):
        module = holder['app'].state.modules[key]
        return module.app is not None and not module.stopping
    app = create_runtime(name="Borges's Hoard", product='borges', root=root, data_dir=data_dir,
        specs=SPECS, port=port, factory=lambda key, path, automatic: factory(key, path, port=port, automatic=automatic,
            native_call=native_call, native_available=native_available))
    holder['app'] = app
    return app
