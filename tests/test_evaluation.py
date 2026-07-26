"""Paso 3 del ROADMAP: evaluación y verificación programática de citas.

Cubre las tres piezas por separado (evaluador, guardrails, registro) y luego
su composición en el motor.
"""
from __future__ import annotations

import json

from kernel.rag.pipeline import Provenance, RAGContext, ScoredChunk
from orchestration.evaluation import CitationEvaluator, Guardrails


def _ctx(*fuentes: str) -> RAGContext:
    chunks = [
        ScoredChunk(f"c{i}", f"pasaje {i}", 0.9, "01-Finanzas",
                    Provenance(source=f, hash=f"h{i}", knowledge_version="wiki:abc"))
        for i, f in enumerate(fuentes, 1)
    ]
    return RAGContext("q", chunks, [c.provenance for c in chunks])


UNA = "01-Finanzas/Inflacion RD.md · «Inflación»"
OTRA = "01-Finanzas/Presupuesto.md · «Presupuesto»"


# -- evaluador de citas --------------------------------------------------------
def test_aprueba_una_respuesta_que_cita_por_indice():
    ev = CitationEvaluator().evaluate(answer="la inflación fue 3.4% [1].",
                                      ctx=_ctx(UNA), rigor=5)
    assert ev.passed is True
    assert ev.cited == [UNA]


def test_aprueba_una_respuesta_que_cita_por_nombre_de_archivo():
    ev = CitationEvaluator().evaluate(answer="según [Inflacion RD.md] fue 3.4%.",
                                      ctx=_ctx(UNA), rigor=5)
    assert ev.passed is True


def test_rechaza_una_respuesta_sin_ninguna_cita():
    ev = CitationEvaluator().evaluate(answer="la inflación fue 3.4%, confía en mí.",
                                      ctx=_ctx(UNA), rigor=5)
    assert ev.passed is False
    assert ev.score == 0.0
    assert "no cita" in ev.reason


def test_rechaza_una_cita_fabricada_aunque_haya_una_valida():
    """Citar algo que no se le dio es peor que no citar: aparenta fundamento."""
    ev = CitationEvaluator().evaluate(
        answer="según [1] y también [informe-secreto.md] la respuesta es X.",
        ctx=_ctx(UNA), rigor=5)
    assert ev.passed is False
    assert ev.fabricated == ["informe-secreto.md"]


def test_rechaza_un_indice_fuera_de_rango():
    ev = CitationEvaluator().evaluate(answer="según [7] la respuesta es X.",
                                      ctx=_ctx(UNA), rigor=5)
    assert ev.passed is False
    assert ev.fabricated == ["7"]


def test_el_rigor_alto_exige_dos_fuentes_distintas():
    una_sola = CitationEvaluator().evaluate(answer="la respuesta es X [1].",
                                            ctx=_ctx(UNA, OTRA), rigor=9)
    assert una_sola.passed is False
    assert una_sola.required_sources == 2

    dos = CitationEvaluator().evaluate(answer="la respuesta es X [1][2].",
                                       ctx=_ctx(UNA, OTRA), rigor=9)
    assert dos.passed is True


def test_el_rigor_alto_no_exige_dos_fuentes_si_solo_habia_una():
    ev = CitationEvaluator().evaluate(answer="la respuesta es X [1].",
                                      ctx=_ctx(UNA), rigor=10)
    assert ev.passed is True
    assert ev.required_sources == 1


def test_sin_evidencia_no_hay_nada_que_citar():
    ev = CitationEvaluator().evaluate(answer="lo que sea [1]", ctx=_ctx(), rigor=5)
    assert ev.passed is False
    assert "no se recuperó evidencia" in ev.reason


# -- guardrails ----------------------------------------------------------------
def test_detecta_el_dominio_por_las_capacidades_del_agente(mini_root):
    g = Guardrails.from_yaml(mini_root / "configs" / "guardrails.yaml")

    finanzas = g.check("cuánto ahorro", ["finanzas_test"])
    assert finanzas.dominios == ["finanzas"]
    assert "asesoría de inversión" in finanzas.avisos[0]

    salud = g.check("cuánto duermo", ["sueno_test"])
    assert salud.dominios == ["salud"]


def test_una_senal_de_urgencia_escala_sin_importar_el_agente(mini_root):
    """Una crisis no deja de serlo porque el enrutado eligiera finanzas."""
    g = Guardrails.from_yaml(mini_root / "configs" / "guardrails.yaml")

    check = g.check("señal de urgencia en mi vida", ["finanzas_test"])

    assert check.escalate is True
    assert check.urgencia_id == "crisis_prueba"
    assert "atención humana" in check.mensaje


def test_las_senales_se_comparan_sin_acentos(mini_root):
    g = Guardrails.from_yaml(mini_root / "configs" / "guardrails.yaml")
    assert g.check("QUIERO HACERME DAÑO", ["finanzas_test"]).escalate is True


def test_una_urgencia_acotada_a_un_dominio_no_aplica_fuera(mini_root):
    g = Guardrails.from_yaml(mini_root / "configs" / "guardrails.yaml")
    consulta = "llevo cinco días sin dormir nada"

    assert g.check(consulta, ["sueno_test"]).escalate is True
    assert g.check(consulta, ["finanzas_test"]).escalate is False


def test_sin_archivo_los_guardrails_quedan_inactivos_de_forma_explicita(tmp_path):
    g = Guardrails.from_yaml(tmp_path / "no-existe.yaml")
    assert g.activo is False
    assert g.check("lo que sea", ["finanzas_test"]).escalate is False


def test_los_guardrails_reales_del_repositorio_cargan(repo_root):
    g = Guardrails.from_yaml(repo_root / "configs" / "guardrails.yaml")
    assert g.activo is True
    # los agentes reales de salud/finanzas deben caer en un dominio
    assert g.check("consulta cualquiera", ["macroeconomics"]).dominios == ["finanzas"]
    assert g.check("consulta cualquiera", ["mental_health"]).dominios == ["salud"]


# -- trazabilidad reproducible -------------------------------------------------
def test_la_provenance_lleva_hash_del_pasaje_y_snapshot_de_la_wiki(mini_root):
    from kernel.rag.file_store import FileWikiStore

    store = FileWikiStore(mini_root / "LLM-Wiki" / "wiki")
    report = store.ingest()
    chunk = store.retrieve("inflación República Dominicana", ["01-Finanzas"], 1)[0]

    assert chunk.provenance.hash, "sin hash una cita no se puede verificar después"
    assert chunk.provenance.knowledge_version == f"wiki:{report['snapshot']}"
    assert chunk.provenance.knowledge_version != "wiki:wiki-live"


def test_el_snapshot_cambia_si_cambia_una_nota(mini_root_mutable):
    from kernel.rag.file_store import FileWikiStore

    wiki = mini_root_mutable / "LLM-Wiki" / "wiki"
    antes = FileWikiStore(wiki).ingest()["snapshot"]

    nota = wiki / "01-Finanzas" / "Inflacion RD.md"
    nota.write_text(nota.read_text(encoding="utf-8") + "\n\nUn párrafo nuevo del usuario.\n",
                    encoding="utf-8")
    despues = FileWikiStore(wiki).ingest()["snapshot"]

    assert antes != despues, "el snapshot debe reflejar el estado real de la wiki"


def test_el_snapshot_es_estable_si_nada_cambia(mini_root):
    from kernel.rag.file_store import FileWikiStore

    wiki = mini_root / "LLM-Wiki" / "wiki"
    assert FileWikiStore(wiki).ingest()["snapshot"] == FileWikiStore(wiki).ingest()["snapshot"]


def test_el_registro_jsonl_guarda_referencias_no_el_texto_de_las_notas(tmp_path):
    from orchestration.audit import JsonlTraceStore, build_entry

    store = JsonlTraceStore(tmp_path / "traces")
    ctx = _ctx(UNA)
    store.record(build_entry(
        agent_id="fina", question="cuál es la inflación", chunks=ctx.chunks,
        evaluation={"aprobada": True}, guardrails={"dominios": ["finanzas"]},
        provider_trace={"modelo_final": "fake-grande"}, mode="llm"))

    archivos = list((tmp_path / "traces").glob("*.jsonl"))
    entrada = json.loads(archivos[0].read_text(encoding="utf-8").strip())

    assert entrada["agente"] == "fina"
    assert entrada["conocimiento"]["version"] == "wiki:abc"
    assert entrada["conocimiento"]["chunks"][0]["hash"] == "h1"
    assert "pasaje 1" not in json.dumps(entrada), "no se duplica el contenido de la nota"
