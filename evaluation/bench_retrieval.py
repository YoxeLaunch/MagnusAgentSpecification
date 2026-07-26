#!/usr/bin/env python
"""Banco de recuperación: mide recall@k del léxico, el vectorial y el híbrido.

El retriever "vectorial" es local por random indexing con pesos TF-IDF
(`kernel/rag/embedder.py`), no un embedder neuronal.

Es la herramienta que responde al criterio de hecho del paso 4 del ROADMAP —
"el set de evaluación de recuperación muestra mejora medible frente al
baseline solo-léxico"— sobre la LLM-Wiki REAL, no sobre fixtures.

Uso:
    python -m evaluation.bench_retrieval [--k 8] [--goldens ruta.yaml]

No usa red ni credenciales: solo recuperación.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from kernel.rag.file_store import FileWikiStore  # noqa: E402
from kernel.rag.pipeline import RAGPipeline, RAGRequest  # noqa: E402
from kernel.rag.vector_store import InMemoryVectorStore  # noqa: E402


@dataclass
class Caso:
    consulta: str
    namespaces: list[str]
    esperado: list[str]

    def acierta(self, fuentes: list[str]) -> bool:
        """Basta con recuperar UNA de las notas esperadas entre las k primeras."""
        return any(any(e.lower() in f.lower() for f in fuentes) for e in self.esperado)


def cargar_casos(path: Path) -> list[Caso]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Caso(c["consulta"], list(c.get("namespaces", [])), list(c["esperado"]))
            for c in raw.get("casos", []) if not c.get("debe_fallar")]


def evaluar(nombre: str, recuperar, casos: list[Caso], k: int) -> tuple[str, float, list[str]]:
    aciertos, fallos = 0, []
    for caso in casos:
        fuentes = recuperar(caso, k)
        if caso.acierta(fuentes):
            aciertos += 1
        else:
            fallos.append(caso.consulta)
    return nombre, aciertos / len(casos) if casos else 0.0, fallos


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Banco de recuperación de Magnus")
    p.add_argument("--k", type=int, default=8, help="profundidad del recall@k")
    p.add_argument("--goldens", default=str(ROOT / "evaluation" / "goldens" / "retrieval.yaml"))
    default_wiki = ROOT / "LLM-Wiki" / "wiki"
    if not default_wiki.exists() and (ROOT / "LLM-Wiki.example" / "wiki").exists():
        default_wiki = ROOT / "LLM-Wiki.example" / "wiki"

    p.add_argument("--wiki", default=str(default_wiki))
    args = p.parse_args(argv)

    casos = cargar_casos(Path(args.goldens))
    if not casos:
        print("no hay casos que evaluar", file=sys.stderr)
        return 1

    store = FileWikiStore(args.wiki)
    reporte = store.ingest()
    vectores = InMemoryVectorStore.from_wiki_store(store)
    hibrido = RAGPipeline(vectores, store)
    solo_lexico = RAGPipeline(store, store)

    print(f"wiki: {reporte['files']} notas · {reporte['chunks']} chunks "
          f"· snapshot {reporte['snapshot'][:12]}")
    print(f"casos: {len(casos)} · recall@{args.k}\n")

    def _lexico(caso: Caso, k: int) -> list[str]:
        return [c.provenance.source for c in store.retrieve(caso.consulta, caso.namespaces, k)]

    def _denso(caso: Caso, k: int) -> list[str]:
        return [c.provenance.source for c in vectores.retrieve(caso.consulta, caso.namespaces, k)]

    def _pipeline(pipeline):
        def _f(caso: Caso, k: int) -> list[str]:
            ctx = pipeline.build_context(RAGRequest(
                query=caso.consulta, namespaces=caso.namespaces, top_k=k,
                min_score=0.0, require_citations=False))
            return [c.provenance.source for c in ctx.chunks]
        return _f

    resultados = [
        evaluar("léxico solo (baseline)", _lexico, casos, args.k),
        evaluar("vectorial local solo", _denso, casos, args.k),
        evaluar("pipeline léxico+léxico (antes)", _pipeline(solo_lexico), casos, args.k),
        evaluar("pipeline híbrido (ahora)", _pipeline(hibrido), casos, args.k),
    ]

    for nombre, recall, _ in resultados:
        print(f"  {nombre:<32} recall@{args.k} = {recall:.1%}")

    print()
    for nombre, _, fallos in resultados:
        if fallos and "híbrido" in nombre:
            print(f"  casos que sigue fallando el híbrido ({len(fallos)}):")
            for f in fallos:
                print(f"    · {f}")

    baseline = resultados[0][1]
    hibrido_recall = resultados[-1][1]
    print(f"\n  delta híbrido vs baseline léxico: {hibrido_recall - baseline:+.1%}")

    # Código de salida distinto de 0 si el híbrido empeora el baseline: así el
    # banco sirve como comprobación en CI y no solo como informe que hay que
    # leer con atención.
    if hibrido_recall < baseline:
        print("\n  FALLO: el híbrido empeora el baseline léxico.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
