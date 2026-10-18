"""Select native data locations in place; never open a native database."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--parent', required=True, type=Path)
parser.add_argument('--data-dir', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
central = args.data_dir or Path(os.environ.get('BORGES_DATA_DIR') or root / 'data')
target = central / 'fusion-modules.json'
if target.exists():
    previous = json.loads(target.read_text(encoding='utf-8'))
    if previous.get('enabled'):
        raise SystemExit('Desactiva todos los módulos antes de seleccionar sus datos.')
locations = {'library': args.parent / "Borges's Hoard" / 'data',
             'links': args.parent / 'Links Hoard' / 'data',
             'paperwork': args.parent / "Kafka's Hoard" / 'data'}
if not all(path.is_dir() for path in locations.values()):
    raise SystemExit('Falta una carpeta de datos: no se ha cambiado la configuración.')
central.mkdir(parents=True, exist_ok=True)
payload = {'enabled': [], 'automatic': [], 'data_paths': {key: str(path.resolve()) for key, path in locations.items()}}
temporary = target.with_suffix('.tmp')
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
temporary.replace(target)
print('Rutas seleccionadas. Todos los módulos y automatismos están desactivados.')
