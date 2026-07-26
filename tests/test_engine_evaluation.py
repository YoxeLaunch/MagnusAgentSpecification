"""Paso 3 compuesto en el motor: citas obligatorias, política y guardrails.

El defecto original: `engine.py` forzaba `require_citations=False` en cada
RAGRequest pese a que cada agente lo declara en su `agent.yaml`, y no existía
ningún evaluador en el runtime real.
"""
from __future__ import annotations

import json

from orchestration.audit import JsonlTraceStore
from orchestration.engine import MagnusEngine
from providers.registry import ProviderRegistry

from magnus_fixtures.fake_provider import (
    citing_provider, hallucinating_provider, uncited_provider,
)


def _providers(mini_root, adaptador) -> ProviderRegistry:
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), {"fake": adaptador},
        retries=0, backoff_s=0.0, sleep=lambda _s: None)


def _engine(mini_root, adaptador, **kw) -> MagnusEngine:
    return MagnusEngine(mini_root, providers=_providers(mini_root, adaptador), **kw)


# -- require_citations por agente ---------------------------------------------
def test_el_motor_usa_el_require_citations_del_agente(mini_root):
    engine = _engine(mini_root, citing_provider("fake"))

    fina = engine.ask("inflación República Dominicana", agent_id="fina")
    dormi = engine.ask("cómo mejorar mi sueño", agent_id="dormi")

    assert fina["traza"]["fina"]["require_citations"] is True
    assert dormi["traza"]["dormi"]["require_citations"] is False


def test_un_agente_estricto_sin_evidencia_declara_la_incertidumbre(mini_root):
    """`vacio` exige citas y su namespace no tiene ni una nota."""
    fake = citing_provider("fake")
    engine = _engine(mini_root, fake)

    r = engine.ask("cualquier cosa sobre finanzas", agent_id="vacio")

    assert r["traza"]["vacio"]["modo"] == "sin_evidencia"
    assert "exige citas" in r["respuesta"]
    assert fake.calls == [], "sin evidencia no se llama al modelo"


# -- política ante evaluación fallida (paso 3.3) -------------------------------
def test_una_respuesta_sin_citas_se_rechaza_en_un_agente_estricto(mini_root):
    engine = _engine(mini_root, uncited_provider("fake"))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert r["traza"]["fina"]["modo"] == "rechazada_por_evaluacion"
    assert r["traza"]["fina"]["evaluacion"]["aprobada"] is False
    assert "confía en mí" not in r["respuesta"], "la respuesta débil no se devuelve"
    assert "No puedo dar esta respuesta" in r["respuesta"]
    assert "Inflacion RD.md" in r["respuesta"], "sí se ofrecen los pasajes reales"


def test_una_cita_fabricada_se_rechaza(mini_root):
    engine = _engine(mini_root, hallucinating_provider("fake"))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert r["traza"]["fina"]["modo"] == "rechazada_por_evaluacion"
    assert r["traza"]["fina"]["evaluacion"]["citas_fabricadas"] == [
        "informe-que-no-existe.md"]


def test_un_agente_no_estricto_conserva_la_respuesta_pero_la_marca(mini_root):
    """Nunca en silencio: la advertencia es visible, no solo en la traza."""
    engine = _engine(mini_root, uncited_provider("fake"))

    r = engine.ask("cómo mejorar mi sueño", agent_id="dormi")

    assert r["traza"]["dormi"]["modo"] == "llm_con_advertencia"
    assert "La respuesta es X" in r["respuesta"]
    assert "⚠" in r["respuesta"] and "sin cita verificable" in r["respuesta"]


def test_una_respuesta_bien_citada_pasa_sin_ruido(mini_root):
    engine = _engine(mini_root, citing_provider("fake"))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert r["traza"]["fina"]["modo"] == "llm"
    assert r["traza"]["fina"]["evaluacion"]["aprobada"] is True
    assert "⚠" not in r["respuesta"]


# -- guardrails de dominio y urgencia (paso 3.4) -------------------------------
def test_anexa_el_aviso_del_dominio_a_la_respuesta(mini_root):
    engine = _engine(mini_root, citing_provider("fake"))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert "no es asesoría de inversión" in r["respuesta"]
    assert r["traza"]["fina"]["guardrails"]["dominios"] == ["finanzas"]


def test_el_aviso_del_dominio_tambien_va_en_el_prompt(mini_root):
    fake = citing_provider("fake")
    engine = _engine(mini_root, fake)

    engine.ask("inflación República Dominicana", agent_id="fina")

    sistema = fake.last.request.messages[0].content
    assert "no es asesoría de inversión" in sistema


def test_una_senal_de_urgencia_corta_el_flujo_antes_del_modelo(mini_root):
    fake = citing_provider("fake")
    engine = _engine(mini_root, fake)

    r = engine.ask("tengo una señal de urgencia y no sé qué hacer", agent_id="dormi")

    assert "Escalado de prueba" in r["respuesta"]
    assert r["agentes"] == [] and r["fuentes"] == []
    assert r["traza"]["_guardrails"]["escalado"] == "crisis_prueba"
    assert fake.calls == [], "no se consulta al modelo ante una urgencia"


def test_la_urgencia_escala_aunque_el_agente_sea_de_otro_dominio(mini_root):
    engine = _engine(mini_root, citing_provider("fake"))

    r = engine.ask("quiero hacerme daño", agent_id="fina")

    assert r["traza"]["_guardrails"]["escalado"] == "crisis_prueba"


# -- registro auditable (paso 3.5) ---------------------------------------------
def test_el_registro_esta_desactivado_por_defecto(mini_root, tmp_path):
    engine = _engine(mini_root, citing_provider("fake"))
    engine.ask("inflación República Dominicana", agent_id="fina")

    assert list(tmp_path.glob("**/*.jsonl")) == []


def test_con_registro_activo_se_guarda_la_decision_y_los_hashes(mini_root, tmp_path):
    destino = tmp_path / "traces"
    engine = _engine(mini_root, uncited_provider("fake"),
                     trace_store=JsonlTraceStore(destino))

    engine.ask("inflación República Dominicana", agent_id="fina")

    entrada = json.loads(list(destino.glob("*.jsonl"))[0].read_text(
        encoding="utf-8").strip().splitlines()[0])

    assert entrada["agente"] == "fina"
    assert entrada["modo"] == "rechazada_por_evaluacion"
    assert entrada["evaluacion"]["aprobada"] is False
    assert entrada["evaluacion"]["razon"]
    assert entrada["conocimiento"]["version"].startswith("wiki:")
    assert all(c["hash"] for c in entrada["conocimiento"]["chunks"])


def test_el_escalado_por_urgencia_tambien_queda_registrado(mini_root, tmp_path):
    destino = tmp_path / "traces"
    engine = _engine(mini_root, citing_provider("fake"),
                     trace_store=JsonlTraceStore(destino))

    engine.ask("quiero hacerme daño", agent_id="dormi")

    entrada = json.loads(list(destino.glob("*.jsonl"))[0].read_text(
        encoding="utf-8").strip())
    assert entrada["modo"] == "escalado_urgencia"
    assert entrada["guardrails"]["escalado"] == "crisis_prueba"
