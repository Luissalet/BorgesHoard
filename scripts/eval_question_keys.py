"""Offline eval of the question keys: recall@1 of question-shaped queries with and without the keys.

No network and no model: a scripted generator returns known questions for known passages, the embedder is the hashing fake.
Each target passage answers a question without using the question's words; a distractor passage shares those words.
Plain search puts the distractor first; with the keys the target is first.

    python scripts/eval_question_keys.py            # prints the table, exit code 1 when the keys do not help
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from borges.config import Config  # noqa: E402
from borges.services import Services  # noqa: E402

# (file, passage text, [questions the passage answers])
CORPUS: list[tuple[str, str, list[str]]] = [
    ("optica.txt", "El gasto en optica del observatorio se revisara durante el proximo ejercicio fiscal, sin cambios previstos en las partidas.",
     ["Cuanto cuesta reparar los espejos del telescopio?", "Que presupuesto hay para la optica del observatorio?"]),
    ("limpieza.txt", "Los espejos del telescopio se limpian cada semana con un pano seco; el telescopio y los espejos se guardan en cajas de madera.",
     ["Como se limpian los espejos del telescopio?"]),
    ("horarios.txt", "El personal del archivo abre las salas a las nueve y las cierra a las cinco, de lunes a viernes, salvo festivos regionales.",
     ["A que hora abre el archivo por las mananas?", "Que dias esta cerrado el archivo?"]),
    ("normas.txt", "Queda prohibido introducir bebidas o alimentos en las salas de consulta; los visitantes dejaran sus bolsas en las taquillas.",
     ["Se puede comer dentro de la sala de lectura?", "Donde se dejan los bolsos al entrar?"]),
    ("catalogo.txt", "El catalogo de manuscritos recoge tres mil quinientas piezas ordenadas por siglo, con su signatura y estado de conservacion.",
     ["Cuantos manuscritos hay catalogados?", "Como estan ordenados los manuscritos?"]),
    ("prestamo.txt", "Los libros de la sala general pueden salir de la biblioteca durante quince dias, renovables una vez si nadie los ha reservado.",
     ["Por cuanto tiempo puedo llevarme un libro prestado?", "Se puede renovar un prestamo?"]),
]

# query -> file that answers it
QUERIES: list[tuple[str, str]] = [
    ("Cuanto cuesta reparar los espejos del telescopio?", "optica.txt"),
    ("A que hora abre el archivo por las mananas?", "horarios.txt"),
    ("Se puede comer dentro de la sala de lectura?", "normas.txt"),
    ("Cuantos manuscritos hay catalogados?", "catalogo.txt"),
    ("Por cuanto tiempo puedo llevarme un libro prestado?", "prestamo.txt"),
]


class ScriptedGenerator:
    """Returns the scripted questions of the passage it is given; `available=False` plays a model that is down."""

    name = "scripted"
    model = "scripted-questions"

    def __init__(self, available: bool = True):
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def generate(self, text: str, n: int = 3) -> list[str]:
        self.calls += 1
        for _, passage, questions in CORPUS:
            if passage[:40] in text:
                return list(questions)[:n]
        return []


def build_library(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, passage, _ in CORPUS:
        (folder / name).write_text(passage + "\n", encoding="utf-8")


def make_services(data_dir: Path, embed_backend: str = "fake") -> Services:
    return Services(Config(data_dir=data_dir, embed_backend=embed_backend, watch=False, autostart=False, data_dir_configured=True))


def rank_of(services: Services, query: str, filename: str, questions: bool, mode: str = "hybrid") -> int | None:
    hits = services.search.search(query, mode, 10, questions=questions)["hits"]
    for position, hit in enumerate(hits, start=1):
        if Path(hit["rel_path"]).name == filename:
            return position
    return None


def run_eval(work: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(work) if work else Path(tmp)
        library = base / "library"
        build_library(library)
        services = make_services(base / "data")
        services.worker.start()
        try:
            services.add_collection("eval", str(library), [], None, False, False)
            services.worker.wait_idle(60)
            services.question_keys.generator_override = ScriptedGenerator()
            services.question_keys.generate(collection_id=1)
            rows = []
            for query, filename in QUERIES:
                rows.append({"query": query, "target": filename,
                             "plain": rank_of(services, query, filename, questions=False),
                             "with_keys": rank_of(services, query, filename, questions=True)})
        finally:
            services.stop()
    total = len(rows)
    return {"rows": rows, "recall_at_1_plain": sum(r["plain"] == 1 for r in rows) / total,
            "recall_at_1_with_keys": sum(r["with_keys"] == 1 for r in rows) / total}


def main() -> int:
    result = run_eval()
    print(f"{'query':58} {'plain':>6} {'keys':>6}")
    for row in result["rows"]:
        print(f"{row['query'][:57]:58} {str(row['plain']):>6} {str(row['with_keys']):>6}")
    print(f"recall@1 plain {result['recall_at_1_plain']:.2f} -> with question keys {result['recall_at_1_with_keys']:.2f}")
    return 0 if result["recall_at_1_with_keys"] > result["recall_at_1_plain"] else 1


if __name__ == "__main__":
    sys.exit(main())
