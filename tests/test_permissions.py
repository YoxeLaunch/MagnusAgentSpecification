"""Paso 5.1 del ROADMAP: enforcement de permisos.

Antes, `permissions.yaml` solo se validaba (que el `policy_ref` existiera).
La política se escribía, se referenciaba y nunca se aplicaba.
"""
from __future__ import annotations

import pytest

from orchestration.engine import MagnusEngine
from orchestration.permissions import PermissionDenied, PermissionEngine

from magnus_fixtures.fake_provider import citing_provider


def _permisos(root) -> PermissionEngine:
    return PermissionEngine.from_yaml(root / "configs" / "permissions.yaml")


def _agente(root, agent_id):
    return MagnusEngine(root, hybrid=False).registry.get(agent_id)


# -- conocimiento ---------------------------------------------------------------
def test_la_parcela_efectiva_es_la_interseccion(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    assert p.allowed_namespaces(fina) == ["01-Finanzas"]
    assert p.can_read(fina, "01-Finanzas").allowed is True


def test_un_namespace_fuera_de_knowledge_sources_se_deniega(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    decision = p.can_read(fina, "02-Sueno")   # la política lo permite, el agente no lo declara

    assert not decision
    assert "no está en knowledge.sources" in decision.reason


def test_una_politica_inexistente_no_concede_nada(mini_root_mutable):
    """Denegación por defecto: sin política válida no se lee nada."""
    yaml_path = mini_root_mutable / "agents" / "_base" / "test_base" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "policy_ref: test_readonly", "policy_ref: politica_fantasma"),
        encoding="utf-8")
    # el registro rechaza el agente, así que se prueba el motor de permisos solo
    p = _permisos(mini_root_mutable)

    class _AgenteFalso:
        id = "x"
        permissions_policy_ref = "politica_fantasma"
        knowledge_sources = ["01-Finanzas"]
        tools_allow = ["llm_wiki"]
        tools_deny: list = []

    assert p.allowed_namespaces(_AgenteFalso()) == []


def test_el_motor_deniega_la_consulta_si_la_politica_no_concede_nada(mini_root_mutable):
    yaml_path = mini_root_mutable / "agents" / "dormi" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "status: active", "status: active\npermissions:\n  policy_ref: test_sin_lectura"),
        encoding="utf-8")

    engine = MagnusEngine(mini_root_mutable, hybrid=False)
    r = engine.ask("cómo mejorar mi sueño", agent_id="dormi")

    assert r["traza"]["dormi"]["modo"] == "denegado_por_permisos"
    assert "no concede lectura" in r["respuesta"]
    assert r["fuentes"] == []


# -- herramientas ---------------------------------------------------------------
def test_las_herramientas_efectivas_son_la_interseccion(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    # el agente hereda allow=[llm_wiki]; la política concede [llm_wiki, python]
    assert p.effective_tools(fina) == {"llm_wiki"}


def test_deny_gana_sobre_allow(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    decision = p.check_tool(fina, "terminal", rol="admin")

    assert not decision
    assert "denegada" in decision.reason or "no declara" in decision.reason


def test_el_rol_del_llamante_tambien_tiene_que_autorizar(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    assert p.check_tool(fina, "llm_wiki", rol="tecnico").allowed is True
    assert p.check_tool(fina, "llm_wiki", rol="admin").allowed is True
    # `operator` puede consultar pero no usar herramientas
    assert p.check_tool(fina, "llm_wiki", rol="operator").allowed is False
    assert p.check_tool(fina, "llm_wiki", rol="rol_inventado").allowed is False


def test_require_tool_lanza_con_el_motivo(mini_root):
    p = _permisos(mini_root)
    fina = _agente(mini_root, "fina")

    with pytest.raises(PermissionDenied) as exc:
        p.require_tool(fina, "terminal", rol="admin")
    assert "terminal" in str(exc.value)


# -- configuración real del repositorio ------------------------------------------
def test_todos_los_agentes_reales_conservan_su_parcela_bajo_enforcement(repo_root):
    """Guardia contra la deriva que este paso destapó.

    `economist_readonly` concedía lectura sobre `economics/`, `finance/`, `imf/`…
    —los namespaces de antes de reorganizar la wiki— mientras `ernesto_libras`
    declara `01-Economia-y-Finanzas`. Como la política nunca se aplicaba, nadie
    se enteró. Si vuelve a divergir, este test falla.
    """
    engine = MagnusEngine(repo_root, hybrid=False)

    huerfanos = {a.id: a.knowledge_sources
                 for a in engine.registry.list(status="active")
                 if not engine.permissions.allowed_namespaces(a)}

    assert huerfanos == {}, (
        f"estos agentes se quedan sin poder leer nada bajo su política: {huerfanos}")


def test_ningun_agente_real_declara_herramientas_que_su_politica_no_concede(repo_root):
    engine = MagnusEngine(repo_root, hybrid=False)

    for a in engine.registry.list(status="active"):
        policy = engine.permissions.policy_for(a)
        sobrantes = set(a.tools_allow) - set(policy.tools_allow) - set(a.tools_deny)
        assert not sobrantes, (
            f"{a.id} declara {sorted(sobrantes)} que '{policy.id}' no concede: "
            f"se denegarían en ejecución, así que la declaración engaña")
