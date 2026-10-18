"""Search active native collections without enabling or copying any source."""
SEARCH_TOOL = {
    'name': 'borges_search_all',
    'description': 'Search active library, saved articles and paperwork, preserving each native citation. Never enables modules.',
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    'inputSchema': {'type': 'object', 'properties': {'q': {'type': 'string', 'minLength': 3, 'maxLength': 200},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30, 'default': 8}}, 'required': ['q']},
}


async def search_all(modules, q, limit, port):
    import asyncio
    async def search(key, tool, args):
        module = modules[key]
        if module.app is None or module.stopping:
            return {'module': key, 'status': 'module_disabled', 'results': []}
        token = (module.data_dir / 'mcp-token').read_text(encoding='utf-8').strip()
        try:
            result = await module.request('POST', '/api/agent/call', json={'name': tool, 'arguments': args},
                headers={'Host': f'127.0.0.1:{port}', 'Authorization': 'Bearer ' + token})
            return {'module': key, 'status': 'ok' if result.status_code == 200 else 'error',
                    'native_result': result.json()}
        except Exception as exc:
            return {'module': key, 'status': 'error', 'error': str(exc)}
    results = await asyncio.gather(search('library', 'library_search', {'q': q, 'limit': limit}),
        search('links', 'search_links', {'q': q, 'limit': limit}),
        search('paperwork', 'doc_search', {'query': q, 'limit': limit}))
    return {'query': q, 'sources': results, 'note': 'Sólo se han consultado los módulos activos; las citas y resultados son los originales.'}
