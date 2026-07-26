"""Ingesta léxica sobre la wiki y política de citas del pipeline RAG."""
from __future__ import annotations

from kernel.rag.file_store import FileWikiStore
from kernel.rag.pipeline import (
    Provenance, RAGPipeline, RAGRequest, ScoredChunk,
)


def _store(mini_root) -> FileWikiStore:
    s = FileWikiStore(mini_root / "LLM-Wiki" / "wiki")
    s.ingest()
    return s


def test_la_ingesta_descubre_namespaces_y_chunks(mini_root):
    s = FileWikiStore(mini_root / "LLM-Wiki" / "wiki")
    report = s.ingest()
    assert report["files"] == 3
    assert report["chunks"] > 0
    assert "01-Finanzas" in report["namespaces"]
    assert "02-Sueno" in report["namespaces"]


def test_la_recuperacion_respeta_el_namespace_del_agente(mini_root):
    s = _store(mini_root)
    resultados = s.retrieve("inflación República Dominicana", ["02-Sueno"], 5)
    assert resultados == [], "un agente no debe ver notas fuera de su parcela"

    resultados = s.retrieve("inflación República Dominicana", ["01-Finanzas"], 5)
    assert resultados
    assert all(c.namespace == "01-Finanzas" for c in resultados)


def test_los_scores_lexicos_reales_superan_el_umbral_de_los_agentes(mini_root):
    """Los umbrales de `agent.yaml` (0.30) deben ser alcanzables de verdad.

    Es la comprobación que justifica el paso 2.4 del ROADMAP: cambiar el
    `min_score` global (0.02) por el del agente solo es correcto si la
    recuperación real puntúa por encima de ese umbral.
    """
    s = _store(mini_root)
    resultados = s.retrieve("inflación en República Dominicana", ["01-Finanzas"], 5)
    assert resultados[0].score >= 0.30


def test_el_pipeline_deduplica_entre_denso_y_lexico(mini_root):
    s = _store(mini_root)
    pipeline = RAGPipeline(s, s)
    ctx = pipeline.build_context(RAGRequest(
        query="inflación República Dominicana", namespaces=["01-Finanzas"],
        top_k=5, min_score=0.30, require_citations=True))
    ids = [c.chunk_id for c in ctx.chunks]
    assert len(ids) == len(set(ids))


class _RetrieverVacio:
    def retrieve(self, query, namespaces, k):
        return []


class _RetrieverFijo:
    def __init__(self, chunks): self._chunks = chunks
    def retrieve(self, query, namespaces, k): return self._chunks[:k]


def test_sin_evidencia_y_con_citas_obligatorias_el_contexto_queda_vacio():
    pipeline = RAGPipeline(_RetrieverVacio(), _RetrieverVacio())
    ctx = pipeline.build_context(RAGRequest(
        query="lo que sea", namespaces=["01-Finanzas"], require_citations=True))
    assert ctx.chunks == []
    assert ctx.citations == []


def test_el_umbral_filtra_los_chunks_debiles():
    debil = ScoredChunk("c1", "texto irrelevante", 0.05, "01-Finanzas",
                        Provenance(source="nota.md"))
    fuerte = ScoredChunk("c2", "texto pertinente", 0.80, "01-Finanzas",
                         Provenance(source="nota.md"))
    pipeline = RAGPipeline(_RetrieverFijo([debil, fuerte]), _RetrieverVacio())
    ctx = pipeline.build_context(RAGRequest(
        query="q", namespaces=["01-Finanzas"], min_score=0.30))
    assert [c.chunk_id for c in ctx.chunks] == ["c2"]
