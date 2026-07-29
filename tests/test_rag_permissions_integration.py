"""Integración RAG ↔ Permisos.

`orchestration/permissions.py` y `kernel/rag/file_store.py` se desarrollaron
y probaron por separado — y esa separación fue exactamente lo que dejó pasar
el defecto real de la Fase 1.3: un "acotamiento a subcarpeta" en
`allowed_namespaces()` que ninguna prueba unitaria de permisos podía detectar,
porque la unidad no sabe que el RAG no indexa subcarpetas. Estos tests cruzan
la frontera entre ambas capas a propósito.
"""
from __future__ import annotations

import pytest

from kernel.rag.file_store import FileWikiStore
from orchestration.engine import MagnusEngine
from orchestration.permissions import NamespaceGranularityError, PermissionEngine


def test_namespaces_del_wiki_store_son_siempre_de_primer_nivel(mini_root):
    """Invariante estructural del que depende `NamespaceGranularityError`
    (permissions.py): si esto deja de cumplirse porque `file_store.py` gana
    granularidad de subcarpeta, ese guard debe relajarse (ver CAMINO.md, 3.3)
    — este test hace explícito el supuesto para que ese cambio no sea
    silencioso."""
    store = FileWikiStore(mini_root / "LLM-Wiki" / "wiki")
    store.ingest()
    for ns in store.namespaces():
        assert "/" not in ns, f"namespace con subcarpeta inesperado: {ns!r}"


def test_namespaces_del_wiki_real_del_repo_son_de_primer_nivel(repo_root):
    """Mismo invariante contra la wiki real del repositorio (o su plantilla
    `.example`), no solo la de test — es la que de verdad importa en
    producción."""
    wiki_dir = repo_root / "LLM-Wiki" / "wiki"
    if not wiki_dir.exists():
        wiki_dir = repo_root / "LLM-Wiki.example" / "wiki"
    store = FileWikiStore(wiki_dir)
    store.ingest()
    for ns in store.namespaces():
        assert "/" not in ns, f"namespace con subcarpeta inesperado: {ns!r}"


def test_allowed_namespaces_de_agentes_reales_no_tiene_subcarpetas(mini_root):
    """Cada namespace que `allowed_namespaces()` entrega a `RAGPipeline` debe
    ser exactamente algo que `FileWikiStore` pueda matchear — nunca una
    subcarpeta fabricada que garantiza cero resultados en silencio."""
    engine = MagnusEngine(mini_root, hybrid=False)
    for a in engine.registry.list(status="active"):
        for ns in engine.permissions.allowed_namespaces(a):
            assert "/" not in ns, (
                f"agente {a.id}: allowed_namespaces() devolvió '{ns}', que el "
                f"RAG (namespaces de primer nivel) nunca podrá matchear")


def test_politica_de_subcarpeta_hace_fallar_la_construccion_del_motor(mini_root_mutable):
    """Extremo a extremo: una política mal escrita (acota por debajo del
    nivel que el RAG indexa) no debe descubrirse en la primera consulta del
    usuario equivocado — `MagnusEngine.__init__` debe fallar al arrancar."""
    yaml_path = mini_root_mutable / "configs" / "permissions.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            '"01-Finanzas/"', '"01-Finanzas/personal"'),
        encoding="utf-8")

    with pytest.raises(NamespaceGranularityError):
        MagnusEngine(mini_root_mutable, hybrid=False)


def test_sin_esa_politica_mal_escrita_el_motor_arranca_normal(mini_root_mutable):
    """Control: el fixture sin modificar (política amplia, coherente con lo
    que declaran los agentes) sigue arrancando sin excepción."""
    engine = MagnusEngine(mini_root_mutable, hybrid=False)
    assert "fina" in engine.registry.list(status="active")[0].id or True  # arrancó
