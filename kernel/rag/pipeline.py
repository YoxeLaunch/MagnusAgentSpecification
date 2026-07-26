"""Pipeline RAG con citas — la ÚNICA vía por la que un agente 'sabe'.

Recuperación híbrida (léxica + vectorial local) → fusión de rankings → rerank
opcional → filtro por umbral y por los namespaces autorizados del agente →
contexto con citas trazables.

Los `Protocol`s de abajo se llaman `DenseRetriever` y `LexicalRetriever` por
el vocabulario habitual de IR, pero "dense" aquí significa *vector denso de
dimensión fija*, no *embedding neuronal*: la implementación que se enchufa hoy
([`vector_store.py`](vector_store.py)) usa random indexing con pesos TF-IDF.
El pipeline no depende de cuál de las dos cosas sea — ese es justamente el
punto del contrato.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Provenance:
    source: str
    date: str | None = None
    hash: str | None = None
    license: str | None = None
    knowledge_version: str | None = None


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    text: str
    score: float
    namespace: str
    provenance: Provenance


@dataclass(frozen=True)
class RAGRequest:
    query: str
    namespaces: list[str]              # parcela del agente (MAS knowledge.sources)
    top_k: int = 8
    min_score: float = 0.35
    require_citations: bool = True


@dataclass(frozen=True)
class RAGContext:
    query: str
    chunks: list[ScoredChunk]
    citations: list[Provenance] = field(default_factory=list)

    def as_prompt_block(self, max_chars: int | None = None) -> str:
        """Evidencia numerada y citable.

        `max_chars` acota el tamaño del contexto que se manda al proveedor
        (paso 2.6 del ROADMAP): se cortan chunks ENTEROS desde el final, nunca
        a mitad de un pasaje — un fragmento truncado sigue arrastrando su cita
        y haría citable algo que ya no dice lo que decía.
        """
        parts = []
        total = 0
        for i, c in enumerate(self.chunks, 1):
            bloque = f"[{i}] ({c.provenance.source}) {c.text}"
            if max_chars is not None and parts and total + len(bloque) > max_chars:
                break
            parts.append(bloque)
            total += len(bloque) + 2
        return "\n\n".join(parts)


class DenseRetriever(Protocol):
    def retrieve(self, query: str, namespaces: list[str], k: int) -> list[ScoredChunk]: ...


class LexicalRetriever(Protocol):
    def retrieve(self, query: str, namespaces: list[str], k: int) -> list[ScoredChunk]: ...


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]: ...


#: Constante de Reciprocal Rank Fusion. 60 es el valor del paper original
#: (Cormack et al., 2009) y amortigua el peso de las primeras posiciones.
RRF_K = 60

#: Cuántos candidatos pedir a cada retriever por cada hueco de `top_k`.
#: RRF fusiona mejor cuanto más profundas son las listas de entrada. Medido en
#: `evaluation/bench_retrieval.py` sobre la wiki real (recall@8): 1→84.2%,
#: 2-4→89.5%, 6-8→94.7%, 10→100%. Se elige 8, en la meseta, y no 10: con 19
#: goldens, exprimir el último punto es ajustar el parámetro al set de
#: evaluación, no mejorar la recuperación. El coste de subirlo es casi nulo
#: mientras los retrievers recorran el índice entero de todos modos; con un
#: vector store remoto (Qdrant) sí habría que acotarlo por latencia.
OVERSAMPLE = 8


class RAGPipeline:
    """Recuperación híbrida con citas.

    Fusión de rankings (paso 4 del ROADMAP): el **orden** lo decide Reciprocal
    Rank Fusion sobre las dos listas, y el **umbral** se aplica sobre el score
    original de cada retriever. Son dos preguntas distintas:

      - "¿qué chunk es más relevante?" → posición relativa en cada ranking, que
        sí es comparable entre un coseno y un TF léxico.
      - "¿es este chunk evidencia suficiente?" → el score en su propia escala,
        que es lo que el agente calibró en `knowledge.retrieval.min_score`.

    Antes se fusionaba con `max(score)` entre las dos listas, lo que compara
    magnitudes de escalas distintas y hacía que el retriever con la escala más
    generosa dominara la selección.
    """

    def __init__(self, dense: DenseRetriever, lexical: LexicalRetriever,
                 reranker: Reranker | None = None, *, oversample: int = OVERSAMPLE):
        self._dense = dense
        self._lexical = lexical
        self._reranker = reranker
        self._oversample = max(1, oversample)

    def build_context(self, req: RAGRequest) -> RAGContext:
        # 1) recuperación híbrida (en producción, en paralelo).
        #    Se piden MÁS candidatos que `top_k` a cada retriever: si a cada uno
        #    se le pidieran solo `top_k`, fusionar dos listas cortas empeora el
        #    resultado en vez de mejorarlo — medido en `evaluation/bench_retrieval.py`,
        #    la fusión sin sobremuestreo caía por debajo del baseline léxico,
        #    porque el retriever más débil desplazaba aciertos del más fuerte.
        profundidad = req.top_k * self._oversample
        dense = self._dense.retrieve(req.query, req.namespaces, profundidad)
        lexical = self._lexical.retrieve(req.query, req.namespaces, profundidad)

        # 2) unión + dedupe por chunk_id, con puntuación RRF para el orden
        merged: dict[str, ScoredChunk] = {}
        rrf: dict[str, float] = {}
        for ranking in (dense, lexical):
            for posicion, c in enumerate(ranking, 1):
                rrf[c.chunk_id] = rrf.get(c.chunk_id, 0.0) + 1.0 / (RRF_K + posicion)
                prev = merged.get(c.chunk_id)
                if prev is None or c.score > prev.score:
                    merged[c.chunk_id] = c
        candidates = list(merged.values())

        # 3) rerank opcional (cross-encoder); si no hay, manda la fusión RRF
        if self._reranker:
            candidates = self._reranker.rerank(req.query, candidates)
        else:
            candidates.sort(key=lambda c: (rrf[c.chunk_id], c.score), reverse=True)

        # 4) filtro por umbral (en la escala propia del chunk) y top_k
        selected = [c for c in candidates if c.score >= req.min_score][: req.top_k]

        # 5) política de citas
        citations = [c.provenance for c in selected]
        if req.require_citations and not citations:
            # sin evidencia suficiente → el agente debe declararlo, no alucinar
            return RAGContext(req.query, [], [])

        return RAGContext(req.query, selected, citations)
