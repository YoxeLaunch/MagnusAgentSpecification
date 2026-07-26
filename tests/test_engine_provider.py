"""Paso 2 del ROADMAP: el motor respeta la configuración real del agente.

Antes de estas correcciones el motor llamaba `provider.complete(req)` con un
solo argumento (el puerto exige dos → `TypeError` con cualquier proveedor real),
forzaba `profile="local_private"` e ignoraba `min_score` del agente. Estos
tests fijan el comportamiento correcto para que no pueda volver a romperse.
"""
from __future__ import annotations

import pytest

from orchestration.engine import MagnusEngine
from providers.base import ProviderError
from providers.registry import ProviderRegistry

from magnus_fixtures.fake_provider import FakeProvider, citing_provider


def _providers(mini_root, *, retries: int = 0, **adaptadores) -> ProviderRegistry:
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), adaptadores,
        retries=retries, backoff_s=0.0, sleep=lambda _s: None)


# -- el defecto original -------------------------------------------------------
def test_el_modo_llm_ejecuta_contra_un_proveedor_real(mini_root):
    """Regresión del `TypeError`: la llamada al puerto usa las dos posiciones."""
    fake = citing_provider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))

    r = engine.ask("cuál es la inflación en República Dominicana", agent_id="fina")

    assert len(fake.calls) == 1
    assert "respuesta generada" in r["respuesta"]
    assert r["traza"]["fina"]["modo"] == "llm"


def test_usa_el_perfil_que_declara_cada_agente_no_uno_hardcodeado(mini_root):
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))

    engine.ask("inflación República Dominicana", agent_id="fina")   # perfil_test
    engine.ask("cómo mejorar mi sueño", agent_id="dormi")            # perfil_barato

    assert fake.profiles_requested == ["perfil_test", "perfil_barato"]
    assert fake.models_used == ["fake-grande", "fake-pequeno"]


def test_usa_el_min_score_del_agente_no_el_global(mini_root):
    """`fina` exige 0.30 en su agent.yaml; el global del motor es 0.02.

    La consulta 'pesos ahorro' recupera un chunk con score 0.205: pasa el
    umbral global pero NO el del agente. Si el motor volviera a usar el valor
    global, este test lo detecta — la respuesta pasaría de 'sin evidencia' a
    una generación fundada en un match débil.
    """
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          min_score=0.02)

    r = engine.ask("pesos ahorro", agent_id="fina")

    assert r["traza"]["fina"]["min_score"] == 0.30
    assert r["traza"]["fina"]["modo"] == "sin_evidencia"
    assert fake.calls == [], "sin evidencia no se debe llamar al modelo"


def test_el_umbral_bajo_de_otro_agente_si_deja_pasar_ese_chunk(mini_root):
    """El mismo motor, otro agente, otro umbral: 'dormi' declara 0.10."""
    fake = citing_provider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))

    r = engine.ask("pantallas antes de dormir", agent_id="dormi")

    assert r["traza"]["dormi"]["min_score"] == 0.10
    assert r["traza"]["dormi"]["modo"] == "llm"


def test_pasa_el_effort_declarado_por_el_agente(mini_root_mutable):
    yaml_path = mini_root_mutable / "agents" / "fina" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "profile: perfil_test", "profile: perfil_test\n  effort: high"),
        encoding="utf-8")

    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root_mutable,
                          providers=_providers(mini_root_mutable, fake=fake))
    engine.ask("inflación República Dominicana", agent_id="fina")

    assert fake.last.request.effort == "high"


# -- fallback y degradación ----------------------------------------------------
def test_aplica_el_fallback_del_agente_y_lo_deja_en_la_traza(mini_root):
    caido = ProviderError("503", retryable=True)
    primario = FakeProvider("fake", fail_with=caido, fail_times=1)
    respaldo = FakeProvider("fake_alterno", fail_with=caido)
    engine = MagnusEngine(mini_root, providers=_providers(
        mini_root, fake=primario, fake_alterno=respaldo))

    r = engine.ask("inflación República Dominicana", agent_id="fina")
    traza = r["traza"]["fina"]

    assert traza["fallback_aplicado"] is True
    assert traza["modelo_final"] == "fake-pequeno"     # perfil_barato = fallback del agente
    assert [i["tipo"] for i in traza["intentos"]] == [
        "primary", "profile_fallback", "agent_fallback"]


def test_sin_adaptador_para_el_perfil_degrada_de_forma_explicita(mini_root_mutable):
    """Un perfil sin adaptador no debe reventar ni fingir que respondió."""
    yaml_path = mini_root_mutable / "agents" / "dormi" / "agent.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "profile: perfil_barato", "profile: perfil_sin_adaptador"),
        encoding="utf-8")

    engine = MagnusEngine(mini_root_mutable, providers=_providers(
        mini_root_mutable, fake=FakeProvider("fake")))

    r = engine.ask("cómo mejorar mi sueño", agent_id="dormi")

    assert r["traza"]["dormi"]["modo"] == "extractivo"
    assert "ningún adaptador disponible" in r["traza"]["dormi"]["motivo"]
    assert "Higiene del sueno.md" in r["respuesta"], "debe seguir citando la wiki real"


def test_un_fallo_de_proveedor_degrada_declarandolo_no_en_silencio(mini_root):
    roto = FakeProvider("fake", fail_with=ProviderError("401 credencial inválida",
                                                       retryable=False, status=401))
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=roto))

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert r["traza"]["fina"]["modo"] == "extractivo_degradado"
    assert "⚠" in r["respuesta"] and "401" in r["respuesta"]


def test_on_provider_error_raise_propaga_el_error_normalizado(mini_root):
    roto = FakeProvider("fake", fail_with=ProviderError("401", retryable=False, status=401))
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=roto),
                          on_provider_error="raise")

    with pytest.raises(ProviderError):
        engine.ask("inflación República Dominicana", agent_id="fina")


def test_sin_proveedor_el_motor_sigue_respondiendo_de_forma_extractiva(mini_root):
    engine = MagnusEngine(mini_root)   # sin proveedor: modo extractivo

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert r["traza"]["fina"]["modo"] == "extractivo"
    assert "Inflacion RD.md" in r["respuesta"]


# -- resiliencia operativa -----------------------------------------------------
def test_el_contexto_enviado_al_proveedor_esta_acotado(mini_root):
    fake = FakeProvider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          max_context_chars=200)

    engine.ask("inflación República Dominicana tasa Banco Central", agent_id="fina")

    evidencia = fake.last.request.messages[-1].content
    assert len(evidencia) < 800, "el bloque de evidencia debe respetar el techo"


def test_un_adaptador_suelto_se_envuelve_en_el_registro(mini_root):
    """Compatibilidad: `provider=` sigue funcionando, pero pasa por el registro."""
    fake = citing_provider("fake", prefijo="desde el adaptador suelto")
    engine = MagnusEngine(mini_root, provider=fake)

    r = engine.ask("inflación República Dominicana", agent_id="fina")

    assert "desde el adaptador suelto" in r["respuesta"]
    assert engine.providers is not None
    assert engine.providers.adapters() == ["fake"]
