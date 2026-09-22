"""Self-test: index a folder into a temporary data dir, run three queries and print citations and timings.

    python scripts/selftest.py <folder> [--fake] [--query "..."]...

Uses the real embedding model unless --fake is given (BORGES_MODELS_DIR is honoured to reuse a download).
Never touches the real data directory.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from borges.config import Config  # noqa: E402
from borges.services import Services  # noqa: E402

DEFAULT_QUERIES = ["¿dónde leí sobre la memoria y el olvido?", "presupuesto observatorio", "senderos que se bifurcan"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", help="Folder with documents to index")
    parser.add_argument("--fake", action="store_true", help="Use the fake embedder (no model download)")
    parser.add_argument("--query", action="append", help="Query to run (repeatable); defaults to three sample queries")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return 2
    models = os.environ.get("BORGES_MODELS_DIR")
    with tempfile.TemporaryDirectory(prefix="borges-selftest-") as tmp:
        config = Config(data_dir=Path(tmp) / "data", embed_backend="fake" if args.fake else "auto",
                        models_dir=Path(models) if models else None, watch=False, autostart=False, data_dir_configured=True)
        services = Services(config)
        services.worker.start()
        try:
            started = time.time()
            ok = services.embedder.ensure_loaded()
            print(f"model: {services.embedder.status()['model']} → {services.embedder.status()['state']} ({time.time() - started:.1f}s)")
            if not ok:
                print(f"  {services.embedder.status()['error']} — continuing with BM25 only")
            started = time.time()
            collection = services.add_collection("selftest", str(folder), [], None, False, False)
            services.worker.wait_idle(3600)
            progress = services.worker.progress(collection.id)
            elapsed = time.time() - started
            counts = services.documents.counts()
            print(f"indexed {counts['documents']} documents / {counts['chunks']} chunks in {elapsed:.1f}s "
                  f"({progress['files_changed']} extracted, {progress['chunks_embedded']} embedded, {progress['error_count']} errors, {counts['needs_ocr']} need OCR)")
            for error in progress["errors"][:5]:
                print(f"  ! {error['path']}: {error['error']}")
            for query in args.query or DEFAULT_QUERIES:
                started = time.time()
                result = services.search.search(query, "hybrid", args.limit)
                ms = (time.time() - started) * 1000
                print(f"\n«{query}» — {result['mode']} — {ms:.0f} ms")
                if not result["hits"]:
                    print("  (no hits)")
                for hit in result["hits"]:
                    snippet = hit["snippet"].replace("<mark>", "[").replace("</mark>", "]")
                    print(f"  {hit['citation']}  ({hit['rel_path']})\n      {snippet[:160]}")
        finally:
            services.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
