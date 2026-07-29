"""Paso 1.4 (unit) + paso 2.1 del ROADMAP (integración): `SqliteMemoryEngine`
como implementación de referencia del puerto `MemoryEngine`, y su conexión
como middleware inyectable en `MagnusEngine.ask()`.
"""
from __future__ import annotations

from orchestration.engine import MagnusEngine
from orchestration.memory import (
    MemoryItem, MemoryScope, MemoryType, NullMemoryEngine, SemanticFact,
    SqliteMemoryEngine,
)
from providers.registry import ProviderRegistry

from magnus_fixtures.fake_provider import FakeProvider


def _providers(mini_root, **adaptadores) -> ProviderRegistry:
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), adaptadores,
        retries=0, backoff_s=0.0, sleep=lambda _s: None)


# -- SqliteMemoryEngine, en aislamiento -----------------------------------------
def test_remember_y_recall_short_term_es_por_sesion():
    m = SqliteMemoryEngine(":memory:")
    scope_a = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1", session_id="s1")
    scope_b = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1", session_id="s2")

    m.remember(scope_a, MemoryItem(text="hablamos de inflación"))
    m.remember(scope_b, MemoryItem(text="hablamos de sueño"))

    assert [it.text for it in m.recall(scope_a, "")] == ["hablamos de inflación"]
    assert [it.text for it in m.recall(scope_b, "")] == ["hablamos de sueño"]


def test_recall_filtra_por_query_en_short_term():
    m = SqliteMemoryEngine(":memory:")
    scope = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1", session_id="s1")
    m.remember(scope, MemoryItem(text="inflación de julio"))
    m.remember(scope, MemoryItem(text="cómo dormir mejor"))

    assert [it.text for it in m.recall(scope, "inflación")] == ["inflación de julio"]


def test_remember_short_term_sin_session_id_falla():
    m = SqliteMemoryEngine(":memory:")
    scope = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1")
    try:
        m.remember(scope, MemoryItem(text="x"))
        assert False, "debía exigir session_id"
    except ValueError as e:
        assert "session_id" in str(e)


def test_consolidate_mueve_short_term_a_long_term():
    m = SqliteMemoryEngine(":memory:")
    scope = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1", session_id="s1")
    m.remember(scope, MemoryItem(text="turno 1"))
    m.remember(scope, MemoryItem(text="turno 2"))

    reporte = m.consolidate("s1")

    assert reporte.items_consolidated == 2
    largo = MemoryScope(MemoryType.LONG_TERM, "fina", "u1")
    assert {it.text for it in m.recall(largo, "")} == {"turno 1", "turno 2"}
    # la sesión consolidada se vacía: no queda short_term residual
    assert m.recall(scope, "") == []


def test_propuesta_semantica_requiere_aprobacion_humana():
    """P7 de la constitución: `approve_proposal` cambia el estado, nunca
    escribe directo a knowledge/ — eso es responsabilidad de otro componente."""
    m = SqliteMemoryEngine(":memory:")
    pid = m.propose_semantic("fina", SemanticFact(text="el bcrd subió tasas", confidence=0.7))

    pendientes = m.list_proposals(status="pending")
    assert [p["id"] for p in pendientes] == [pid]

    m.approve_proposal(pid, reviewer="jose")
    aprobadas = m.list_proposals(status="approved")
    assert aprobadas[0]["reviewer"] == "jose"


def test_propuesta_rechazada_registra_motivo():
    m = SqliteMemoryEngine(":memory:")
    pid = m.propose_semantic("fina", SemanticFact(text="dato dudoso"))
    m.reject_proposal(pid, reviewer="jose", reason="sin evidencia suficiente")

    rechazadas = m.list_proposals(status="rejected")
    assert rechazadas[0]["reason"] == "sin evidencia suficiente"


def test_null_memory_engine_no_recuerda_nada():
    m = NullMemoryEngine()
    scope = MemoryScope(MemoryType.SHORT_TERM, "fina", "u1", session_id="s1")
    m.remember(scope, MemoryItem(text="x"))
    assert m.recall(scope, "") == []


# -- integración: MagnusEngine.ask() como middleware ----------------------------
def test_sin_session_id_no_se_graba_ni_se_recupera_memoria_de_sesion(mini_root):
    """Comportamiento por defecto (nadie pasa user_id/session_id): equivalente
    a no tener memoria conversacional, aunque se haya inyectado un motor real."""
    memoria = SqliteMemoryEngine(":memory:")
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          memory=memoria)

    engine.ask("inflación República Dominicana", agent_id="fina")

    scope = MemoryScope(MemoryType.SHORT_TERM, "fina", "anonimo", session_id="lo-que-sea")
    assert memoria.recall(scope, "") == []


def test_memoria_de_sesion_se_recupera_en_el_turno_siguiente(mini_root):
    """El flujo completo del middleware: primer turno graba, segundo turno
    recibe ese turno como contexto en el prompt del sistema (nunca como
    'evidencia' citable de la wiki)."""
    memoria = SqliteMemoryEngine(":memory:")
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          memory=memoria)

    engine.ask("inflación República Dominicana", agent_id="fina",
               user_id="jose", session_id="s1")
    # Debe recuperar evidencia propia (tasa de política monetaria) para llegar
    # a llamar al LLM — si no hay chunks, `_preparar_respuesta` resuelve en
    # 'sin_evidencia' sin construir ningún prompt, y el test no probaría nada.
    engine.ask("tasa de política monetaria del banco central", agent_id="fina",
               user_id="jose", session_id="s1")

    segunda_llamada = fake.calls[-1].request
    system_msg = segunda_llamada.messages[0].content
    assert "Memoria de conversaciones previas" in system_msg
    assert "inflación República Dominicana" in system_msg
    assert "NO es evidencia de tu wiki" in system_msg


def test_memoria_es_por_agente_no_se_filtra_entre_agentes(mini_root):
    memoria = SqliteMemoryEngine(":memory:")
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          memory=memoria)

    engine.ask("inflación República Dominicana", agent_id="fina",
               user_id="jose", session_id="s1")
    engine.ask("cómo mejorar mi sueño", agent_id="dormi",
               user_id="jose", session_id="s1")

    system_msg_dormi = fake.calls[-1].request.messages[0].content
    assert "Memoria de conversaciones previas" not in system_msg_dormi
