"""El Agent Registry es la única fuente de verdad de qué agentes existen."""
from __future__ import annotations

from orchestration.registry.agent_registry import AgentRegistry
from orchestration.registry.capability_catalog import CapabilityCatalog


def _registry(root):
    caps = CapabilityCatalog(root / "capabilities").load_all()
    return AgentRegistry(
        root / "agents", capabilities=caps,
        models_yaml=root / "configs" / "models.yaml",
        permissions_yaml=root / "configs" / "permissions.yaml",
        mcp_catalog_yaml=root / "tools" / "mcp_catalog.yaml",
    )


def test_carga_los_agentes_del_proyecto_minimo(mini_root):
    report = _registry(mini_root).load_all()
    assert sorted(report.loaded) == ["dormi", "fina", "vacio"]
    assert report.invalid == []


def test_no_carga_las_bases_de_herencia_como_agentes(mini_root):
    report = _registry(mini_root).load_all()
    assert "test_base" not in report.loaded
    assert "_base" not in report.loaded


def test_el_spec_expone_la_configuracion_por_agente(mini_root):
    reg = _registry(mini_root)
    reg.load_all()
    fina = reg.get("fina")
    assert fina.top_k == 4
    assert fina.min_score == 0.30
    assert fina.model_profile == "perfil_test"
    assert fina.fallback_profile == "perfil_barato"
    assert fina.require_citations is True

    dormi = reg.get("dormi")
    assert dormi.require_citations is False       # sobreescribe la base
    assert dormi.fallback_profile is None
    assert dormi.min_score == 0.10


def test_hereda_de_la_base_lo_que_el_agente_no_declara(mini_root):
    reg = _registry(mini_root)
    reg.load_all()
    fina = reg.get("fina")
    assert fina.permissions_policy_ref == "test_readonly"   # viene de test_base
    assert fina.rigor == 8                                   # viene de test_base
    assert "email" in fina.tools_deny
    assert fina.inheritance_chain == ["test_base", "fina"]


def test_rechaza_un_perfil_de_modelo_inexistente(mini_root_mutable):
    yaml_path = mini_root_mutable / "agents" / "fina" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "profile: perfil_test", "profile: perfil_que_no_existe"),
        encoding="utf-8")

    report = _registry(mini_root_mutable).load_all()
    assert "fina" not in report.loaded
    errores = [e for v in report.invalid for e in v.errors]
    assert any("perfil_que_no_existe" in e for e in errores)


def test_rechaza_una_capacidad_inexistente(mini_root_mutable):
    yaml_path = mini_root_mutable / "agents" / "dormi" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "id: sueno_test, strength: primary", "id: capacidad_fantasma, strength: primary"),
        encoding="utf-8")

    report = _registry(mini_root_mutable).load_all()
    assert "dormi" not in report.loaded
    errores = [e for v in report.invalid for e in v.errors]
    assert any("capacidad_fantasma" in e for e in errores)


def test_los_agentes_reales_del_repositorio_validan(repo_root):
    """Guardia contra regresiones en agents/ y configs/ de producción."""
    caps = CapabilityCatalog(repo_root / "capabilities").load_all()
    reg = AgentRegistry(
        repo_root / "agents", capabilities=caps,
        models_yaml=repo_root / "configs" / "models.yaml",
        permissions_yaml=repo_root / "configs" / "permissions.yaml",
        mcp_catalog_yaml=repo_root / "tools" / "mcp_catalog.yaml",
    )
    report = reg.load_all()
    assert report.invalid == [], [f"{v.agent_id}: {v.errors}" for v in report.invalid]
    assert report.loaded, "el repositorio no tiene ningún agente cargable"
