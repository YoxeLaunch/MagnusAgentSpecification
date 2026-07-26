"""Enrutado seguro: sin dominio identificado, el motor lo dice.

Antes, cuando ninguna capacidad alcanzaba el umbral, `_route` devolvía
`registry.list(status="active")[:1]` — el primer agente activo por orden
alfabético. Eso contestaba una consulta de salud con el agente de finanzas
porque su `id` empieza por 'a', recuperaba de la parcela equivocada y
presentaba el resultado con la misma apariencia de fundamento que una
respuesta bien enrutada.
"""
from __future__ import annotations

import json

import pytest

from orchestration.audit import JsonlTraceStore
from orchestration.engine import ROUTING_MIN_SCORE, MagnusEngine

from magnus_fixtures.fake_provider import citing_provider


SIN_DOMINIO = "xyzzy plugh frobnicate garble"


# -- match válido ----------------------------------------------------------------
def test_una_consulta_con_dominio_claro_se_enruta(mini_root):
    engine = MagnusEngine(mini_root)

    r = engine.ask("cuál es la inflación en República Dominicana")

    assert "fina" in r["agentes"]
    assert "_enrutado" not in r["traza"]


def test_cada_dominio_va_a_su_agente(mini_root):
    engine = MagnusEngine(mini_root)

    assert engine.ask("cuántas horas debo dormir")["agentes"] == ["dormi"]
    assert "fina" in engine.ask("qué hace el banco central con las tasas")["agentes"]


# -- sin match --------------------------------------------------------------------
def test_sin_dominio_no_se_elige_ningun_agente(mini_root):
    engine = MagnusEngine(mini_root)

    r = engine.ask(SIN_DOMINIO)

    assert r["agentes"] == []
    assert r["fuentes"] == []
    assert r["traza"]["_enrutado"]["modo"] == "sin_dominio"


def test_sin_dominio_la_respuesta_lo_declara_y_no_inventa(mini_root):
    engine = MagnusEngine(mini_root)

    respuesta = engine.ask(SIN_DOMINIO)["respuesta"]

    assert "No identifiqué ningún dominio" in respuesta
    assert "no la voy a responder" in respuesta
    # y ofrece la salida útil: dirigir la consulta a un agente concreto
    for agent_id in ("fina", "dormi", "vacio"):
        assert agent_id in respuesta


def test_sin_dominio_no_se_consulta_al_modelo(mini_root):
    """Ni proveedor ni recuperación: no hay parcela sobre la que buscar."""
    from providers.registry import ProviderRegistry

    fake = citing_provider("fake")
    engine = MagnusEngine(mini_root, providers=ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), {"fake": fake}))

    # el proveedor SÍ está conectado: una consulta con dominio lo usa
    engine.ask("cuál es la inflación en República Dominicana")
    assert fake.calls, "precondición: el fake está realmente enchufado"
    llamadas_previas = len(fake.calls)

    r = engine.ask(SIN_DOMINIO)

    assert len(fake.calls) == llamadas_previas, "no debe gastarse una llamada al modelo"
    assert r["agentes"] == []


def test_sin_dominio_no_cae_en_el_primer_agente_alfabetico(mini_root):
    """Regresión directa del defecto: 'dormi' es el primero por orden."""
    engine = MagnusEngine(mini_root)
    primero = engine.registry.list(status="active")[0].id
    assert primero == "dormi", "el fixture debe tener un primer agente estable"

    assert engine.ask(SIN_DOMINIO)["agentes"] == []


def test_la_traza_explica_por_que_no_hubo_enrutado(mini_root):
    engine = MagnusEngine(mini_root)

    traza = engine.ask(SIN_DOMINIO)["traza"]["_enrutado"]

    assert traza["umbral_enrutado"] == ROUTING_MIN_SCORE
    assert set(traza["agentes_activos"]) == {"fina", "dormi", "vacio"}
    assert isinstance(traza["capacidades_mas_cercanas"], list)


def test_el_umbral_de_enrutado_es_configurable(mini_root):
    """Subirlo vuelve al motor más reticente; es una decisión explícita.

    'pantallas antes de dormir' puntúa 0.395 contra `sueno_test`: pasa el
    umbral por defecto (0.35) y no uno de 0.60.
    """
    consulta = "pantallas antes de dormir"

    assert MagnusEngine(mini_root, routing_min_score=0.35).ask(consulta)["agentes"] == ["dormi"]
    assert MagnusEngine(mini_root, routing_min_score=0.60).ask(consulta)["agentes"] == []


def test_sin_dominio_queda_registrado_en_la_auditoria(mini_root, tmp_path):
    destino = tmp_path / "traces"
    engine = MagnusEngine(mini_root, trace_store=JsonlTraceStore(destino))

    engine.ask(SIN_DOMINIO)

    entrada = json.loads(list(destino.glob("*.jsonl"))[0].read_text(
        encoding="utf-8").strip())
    assert entrada["modo"] == "sin_dominio"
    assert entrada["consulta"] == SIN_DOMINIO


# -- agente forzado ----------------------------------------------------------------
def test_forzar_un_agente_sigue_funcionando(mini_root):
    engine = MagnusEngine(mini_root)

    r = engine.ask("cuál es la inflación en República Dominicana", agent_id="fina")

    assert r["agentes"] == ["fina"]


def test_forzar_un_agente_salta_el_umbral_de_enrutado(mini_root):
    """Si el usuario elige el agente, no hay nada que enrutar."""
    engine = MagnusEngine(mini_root, routing_min_score=0.99)

    r = engine.ask("presupuesto personal mensual", agent_id="fina")

    assert r["agentes"] == ["fina"]
    assert "_enrutado" not in r["traza"]


def test_forzar_un_agente_inexistente_falla_de_forma_explicita(mini_root):
    engine = MagnusEngine(mini_root)

    with pytest.raises(KeyError):
        engine.ask("lo que sea", agent_id="agente_que_no_existe")


# -- interacción con los guardrails --------------------------------------------------
def test_una_urgencia_escala_aunque_no_haya_dominio_identificado(mini_root):
    """La seguridad no puede depender de que el enrutado acierte."""
    engine = MagnusEngine(mini_root)

    r = engine.ask(f"{SIN_DOMINIO} y quiero hacerme daño")

    assert r["traza"]["_guardrails"]["escalado"] == "crisis_prueba"
    assert "_enrutado" not in r["traza"], "la urgencia tiene prioridad sobre el enrutado"


# -- configuración real del repositorio ----------------------------------------------
def test_las_consultas_del_banco_de_recuperacion_encuentran_dominio(repo_root):
    """Un umbral tan alto que nada enrute dejaría el sistema inservible.

    Se comprueba con las consultas del banco de recuperación, que están
    escritas como las escribiría el usuario. No se exige el 100%: el enrutado
    léxico es lo que es, y su mejora es trabajo del paso 6. Se exige que la
    mayoría enrute, para detectar una regresión que rompa el enrutado entero.
    """
    from evaluation.bench_retrieval import cargar_casos

    engine = MagnusEngine(repo_root, hybrid=False)
    casos = cargar_casos(repo_root / "evaluation" / "goldens" / "retrieval.yaml")

    enrutadas = sum(1 for c in casos if engine._route(c.consulta))

    assert enrutadas >= len(casos) * 0.6, (
        f"solo {enrutadas}/{len(casos)} consultas encuentran dominio; "
        f"el enrutado por capacidades está roto, no solo impreciso")
