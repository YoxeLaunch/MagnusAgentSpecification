"""Tests de `sdk/scaffold.py` — las plantillas que genera `magnus agent create`.

Fase 1.4 residual (ver CAMINO.md): lo que hace valioso el scaffold no es que
genere *algo*, sino que genere exactamente lo que `AgentRegistry` exige — los
12 Markdown de `REQUIRED_MD_FILES` con sus placeholders `<!-- completa -->`
(que bloquean `status: active` hasta completarse) y un `agent.yaml`
estructuralmente válido. Estos tests fijan ese contrato.
"""
from __future__ import annotations

import yaml

from orchestration.registry.agent_registry import REQUIRED_MD_FILES
from sdk import scaffold


# -- agent_yaml -----------------------------------------------------------------
def _agent_yaml(**overrides) -> str:
    kw = dict(
        agent_id="serena", name="Serena", role="Agente de bienestar",
        capabilities=[("mental_health", "primary")], extends=None,
        profile="reasoning_std", priority=5, sources=["03-Salud-Mental"],
        policy_ref="wellbeing_readonly",
    )
    kw.update(overrides)
    return scaffold.agent_yaml(**kw)


def test_agent_yaml_es_yaml_valido_y_parseable():
    doc = yaml.safe_load(_agent_yaml())

    assert doc["id"] == "serena"
    assert doc["name"] == "Serena"
    assert doc["status"] == "draft", "todo agente nuevo nace en draft, nunca active"
    assert doc["model"]["profile"] == "reasoning_std"
    assert doc["permissions"]["policy_ref"] == "wellbeing_readonly"


def test_agent_yaml_vuelca_las_capacidades_pedidas():
    doc = yaml.safe_load(_agent_yaml(
        capabilities=[("mental_health", "primary"), ("nutrition", "secondary")]))

    caps = doc["routing"]["capabilities"]
    assert {"id": "mental_health", "strength": "primary"} in caps
    assert {"id": "nutrition", "strength": "secondary"} in caps


def test_agent_yaml_sin_extends_no_declara_la_clave():
    doc = yaml.safe_load(_agent_yaml(extends=None))
    assert "extends" not in doc


def test_agent_yaml_con_extends_declara_la_plantilla():
    doc = yaml.safe_load(_agent_yaml(extends="health_advisor"))
    assert doc["extends"] == "health_advisor"


def test_agent_yaml_sin_sources_produce_lista_vacia_valida():
    doc = yaml.safe_load(_agent_yaml(sources=[]))
    assert doc["knowledge"]["sources"] == []


def test_agent_yaml_con_sources_las_lista_todas():
    doc = yaml.safe_load(_agent_yaml(sources=["03-Salud-Mental", "04-Nutricion"]))
    assert doc["knowledge"]["sources"] == ["03-Salud-Mental", "04-Nutricion"]


def test_agent_yaml_declara_fallback_profile_y_tools_por_defecto():
    """El scaffold no debe generar un agente sin red de respaldo ni tools acotadas."""
    doc = yaml.safe_load(_agent_yaml())
    assert doc["model"]["fallback_profile"] == "routing_fast"
    assert doc["tools"]["allow"] == ["llm_wiki"]
    assert "terminal" in doc["tools"]["deny"]


# -- markdown_files ---------------------------------------------------------------
def test_markdown_files_genera_exactamente_los_requeridos_por_agent_registry():
    """Si esto diverge, `agent activate` fallará o creará un agente que
    `AgentRegistry` nunca activará — el registro es la fuente de verdad."""
    generados = scaffold.markdown_files("Serena", "Agente de bienestar")
    assert set(generados) == set(REQUIRED_MD_FILES)


def test_markdown_files_interpola_nombre_y_rol():
    generados = scaffold.markdown_files("Serena", "Agente de bienestar")
    assert "Serena" in generados["identity.md"]
    assert "Agente de bienestar" in generados["identity.md"]
    assert "Serena" in generados["mission.md"]


def test_markdown_files_trae_placeholders_que_bloquean_la_activacion():
    """`AgentRegistry.validate_as(..., 'active')` rechaza cualquier archivo con
    '<!-- completa'; el scaffold debe generarlos así por diseño (draft, no
    listo para producción) — un test que exigiera lo contrario estaría mal."""
    generados = scaffold.markdown_files("Serena", "Agente de bienestar")
    for filename in REQUIRED_MD_FILES:
        assert "<!-- completa" in generados[filename], (
            f"{filename} debería traer al menos un placeholder sin completar")


def test_markdown_files_no_deja_llaves_de_formato_sin_interpolar():
    """Un '{name}' o '{role}' literal en la salida indica una plantilla mal
    formateada (p.ej. una llave sin escapar en el propio placeholder)."""
    generados = scaffold.markdown_files("Serena", "Agente de bienestar")
    for filename, content in generados.items():
        assert "{name}" not in content, f"{filename} dejó '{{name}}' sin interpolar"
        assert "{role}" not in content, f"{filename} dejó '{{role}}' sin interpolar"


# -- capability_yaml --------------------------------------------------------------
def test_capability_yaml_es_yaml_valido():
    text = scaffold.capability_yaml(
        cap_id="brand_strategy", name="Brand Strategy",
        description="Estrategia de marca", parent=None, examples=[])
    doc = yaml.safe_load(text)

    assert doc["id"] == "brand_strategy"
    assert doc["name"] == "Brand Strategy"
    assert doc["parent"] is None
    assert doc["embedding_seed"] == "auto"


def test_capability_yaml_sin_parent_produce_null_no_string():
    text = scaffold.capability_yaml(
        cap_id="x", name="X", description="d", parent=None, examples=[])
    assert yaml.safe_load(text)["parent"] is None


def test_capability_yaml_con_parent_lo_declara():
    text = scaffold.capability_yaml(
        cap_id="x", name="X", description="d", parent="finance", examples=[])
    assert yaml.safe_load(text)["parent"] == "finance"


def test_capability_yaml_sin_examples_trae_placeholder():
    text = scaffold.capability_yaml(
        cap_id="x", name="X", description="d", parent=None, examples=[])
    doc = yaml.safe_load(text)
    assert doc["routing_examples"] == ["<!-- completa: frase de ejemplo -->"]


def test_capability_yaml_con_examples_los_lista_todos():
    text = scaffold.capability_yaml(
        cap_id="x", name="X", description="d", parent=None,
        examples=["¿cómo posiciono mi marca?", "necesito un naming"])
    doc = yaml.safe_load(text)
    assert doc["routing_examples"] == ["¿cómo posiciono mi marca?", "necesito un naming"]
