"""Paso 5.4 del ROADMAP: política de egreso de datos de la wiki.

Lo que se protege: que notas de salud o salud mental viajen a un proveedor
remoto dentro de un prompt por una configuración implícita o por el fallback
automático.
"""
from __future__ import annotations

import pytest

from orchestration.engine import MagnusEngine
from orchestration.privacy import EgressPolicy
from providers.base import LLMRequest, Message, Role
from providers.registry import ProviderRegistry, ProviderUnavailable

from magnus_fixtures.fake_provider import citing_provider


def _providers(mini_root, **adaptadores) -> ProviderRegistry:
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), adaptadores,
        retries=0, backoff_s=0.0, sleep=lambda _s: None,
        local_providers={"fake_local", "ollama"})


# -- política --------------------------------------------------------------------
def test_deniega_por_defecto_lo_que_nadie_autorizo(mini_root):
    p = EgressPolicy.from_yaml(mini_root / "configs" / "privacy.yaml")

    assert p.check(["01-Finanzas"]).remote_allowed is True
    assert p.check(["88-Carpeta-Nueva-Del-Usuario"]).remote_allowed is False


def test_basta_un_namespace_restringido_para_bloquear(mini_root_mutable):
    """El prompt es uno solo y viaja entero."""
    cfg = mini_root_mutable / "configs" / "privacy.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "    03-Privado: local_only\n",
                   encoding="utf-8")
    p = EgressPolicy.from_yaml(cfg)

    decision = p.check(["01-Finanzas", "03-Privado"])

    assert decision.remote_allowed is False
    assert decision.blocking_namespaces == ("03-Privado",)


def test_sin_archivo_la_postura_es_la_restrictiva(tmp_path):
    p = EgressPolicy.from_yaml(tmp_path / "no-existe.yaml")

    assert p.activa is False
    assert p.check(["lo-que-sea"]).remote_allowed is False


def test_la_politica_real_protege_salud_y_salud_mental(repo_root):
    p = EgressPolicy.from_yaml(repo_root / "configs" / "privacy.yaml")

    assert p.check(["02-Salud-Corporal"]).remote_allowed is False
    assert p.check(["03-Salud-Mental-y-Desarrollo-Personal"]).remote_allowed is False
    assert p.check(["04-Dinamica-Social-y-Atraccion"]).remote_allowed is False
    assert p.check(["06-Tecnologia-e-IA"]).remote_allowed is True


# -- el registro poda los proveedores remotos ------------------------------------
def test_only_local_no_usa_un_proveedor_remoto_ni_como_fallback(mini_root):
    remoto = citing_provider("fake")
    reg = _providers(mini_root, fake=remoto)
    req = LLMRequest(messages=[Message(Role.USER, "hola")], profile="perfil_test")

    with pytest.raises(ProviderUnavailable) as exc:
        reg.complete(req, fallback_profile="perfil_barato", only_local=True)

    assert remoto.calls == [], "el fallback no puede ser la puerta trasera del egreso"
    assert "prohíbe el egreso remoto" in str(exc.value)


def test_only_local_si_hay_endpoint_local_lo_usa(mini_root_mutable):
    models = mini_root_mutable / "configs" / "models.yaml"
    models.write_text(models.read_text(encoding="utf-8").replace(
        "    fallback: { provider: fake_alterno, model: \"fake-respaldo\" }",
        "    fallback: { provider: fake_local, model: \"modelo-en-casa\" }"),
        encoding="utf-8")

    remoto, local = citing_provider("fake"), citing_provider("fake_local")
    reg = _providers(mini_root_mutable, fake=remoto, fake_local=local)
    req = LLMRequest(messages=[Message(Role.USER, "hola")], profile="perfil_test")

    resp = reg.complete(req, only_local=True)

    assert resp.model == "modelo-en-casa"
    assert remoto.calls == []


# -- el motor lo aplica -----------------------------------------------------------
def test_el_motor_no_manda_evidencia_restringida_a_un_proveedor_remoto(mini_root_mutable):
    cfg = mini_root_mutable / "configs" / "privacy.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "02-Sueno: remote_allowed", "02-Sueno: local_only"), encoding="utf-8")

    remoto = citing_provider("fake")
    engine = MagnusEngine(mini_root_mutable, providers=_providers(mini_root_mutable, fake=remoto))

    r = engine.ask("cómo mejorar mi sueño", agent_id="dormi")
    traza = r["traza"]["dormi"]

    assert remoto.calls == [], "los pasajes no deben salir del dispositivo"
    assert traza["egreso"]["egreso_remoto"] is False
    assert traza["modo"] == "extractivo"
    assert "Higiene del sueno.md" in r["respuesta"], "sigue respondiendo con la wiki"


def test_un_namespace_autorizado_si_llega_al_proveedor_remoto(mini_root):
    remoto = citing_provider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=remoto))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert len(remoto.calls) == 1
    assert r["traza"]["fina"]["egreso"]["egreso_remoto"] is True


def test_la_decision_de_egreso_queda_en_la_traza(mini_root_mutable):
    cfg = mini_root_mutable / "configs" / "privacy.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "02-Sueno: remote_allowed", "02-Sueno: local_only"), encoding="utf-8")

    engine = MagnusEngine(mini_root_mutable,
                          providers=_providers(mini_root_mutable, fake=citing_provider("fake")))
    r = engine.ask("cómo mejorar mi sueño", agent_id="dormi")

    egreso = r["traza"]["dormi"]["egreso"]
    assert egreso["namespaces_que_bloquean"] == ["02-Sueno"]
    assert "local_only" in egreso["razon"]
