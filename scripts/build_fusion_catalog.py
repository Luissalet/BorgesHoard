"""Build schemas without opening histories or starting any engine."""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from borges.agent_tools import tool_catalog as library_tools
from kafka_hoard.agent_tools import tool_catalog as paperwork_tools

script = """
import { TOOLS } from './links_native/server/agent-tools.js';
import { z } from './links_native/node_modules/zod/index.js';
import { makeAgentRoutes } from './links_native/server/hoard-commons/express.js';
console.log(JSON.stringify(makeAgentRoutes({app:'links',tools:TOOLS,z}).catalog()));
"""
links = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT, text=True))
catalogs = {'library': library_tools(), 'links': links, 'paperwork': paperwork_tools()}
counts = Counter(t['name'] for items in catalogs.values() for t in items)
out = []
for key, tools in catalogs.items():
    for native in tools:
        tool = dict(native, module=key, native_name=native['name'])
        if counts[native['name']] > 1:
            tool['name'] = key + '_' + native['name']
        out.append(tool)
assert len({t['name'] for t in out}) == len(out)
(ROOT / 'fusion/catalog.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
lines = ['# Preserved native tools', '', '| Module | Tools |', '|---|---:|']
lines += [f'| {key} | {len(tools)} |' for key, tools in catalogs.items()]
lines += ['', '| Module | Published name | Native name |', '|---|---|---|']
lines += [f"| {t['module']} | `{t['name']}` | `{t['native_name']}` |" for t in out]
(ROOT / 'provenance/CAPABILITIES.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({key:len(tools) for key,tools in catalogs.items()}))
