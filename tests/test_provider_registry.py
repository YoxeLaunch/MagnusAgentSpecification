"""Resolución perfil → (provider, model, params) y política de fallback.

La política que se prueba aquí está declarada en el docstring de
`providers/registry.py` y responde al paso 2.3 del ROADMAP.
"""
from __future__ import annotations

import pytest

from providers.base import LLMRequest, Message, ProviderError, Role
from providers.registry import (
    ProfileNotFound, ProviderRegistry, ProviderUnavailable,
)

from magnus_fixtures.fake_provider import FakeProvider


def _req(profile: str) -> LLMRequest:
    return LLMRequest(messages=[Message(Role.USER, "hola")], profile=profile)


def _registry(mini_root, *, retries: int = 0, **providers) -> ProviderRegistry:
    """Registro sobre los perfiles del proyecto mínimo.

    `retries=0` y un `sleep` que no duerme por defecto: ningún test debe pagar
    un backoff real salvo el que prueba explícitamente los reintentos.
    """
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), providers,
        retries=retries, backoff_s=0.0, sleep=lambda _s: None)


# -- resolución ---------------------------------------------------------------
def test_resuelve_el_perfil_al_modelo_declarado(mini_root):
    fake = FakeProvider("fake")
    reg = _registry(mini_root, fake=fake)

    resp = reg.complete(_req("perfil_test"))

    assert resp.model == "fake-grande"
    assert fake.last.resolved.provider == "fake"
    assert fake.last.resolved.params == {"temperature": 0.0}


def test_perfiles_distintos_resuelven_a_modelos_distintos(mini_root):
    fake = FakeProvider("fake")
    reg = _registry(mini_root, fake=fake)

    reg.complete(_req("perfil_test"))
    reg.complete(_req("perfil_barato"))

    assert fake.models_used == ["fake-grande", "fake-pequeno"]


def test_un_perfil_inexistente_falla_con_mensaje_claro_no_con_keyerror(mini_root):
    reg = _registry(mini_root, fake=FakeProvider("fake"))

    with pytest.raises(ProfileNotFound) as exc:
        reg.complete(_req("perfil_que_nadie_declaro"))

    assert "perfil_que_nadie_declaro" in str(exc.value)
    assert "perfil_test" in str(exc.value), "debe listar los perfiles que sí existen"


def test_un_proveedor_sin_adaptador_falla_claro_no_con_keyerror(mini_root):
    """`models.yaml` puede declarar proveedores que no tienen adaptador."""
    reg = _registry(mini_root, fake=FakeProvider("fake"))

    with pytest.raises(ProviderUnavailable) as exc:
        reg.complete(_req("perfil_sin_adaptador"))

    assert "proveedor_inexistente" in str(exc.value)
    assert "fake" in str(exc.value), "debe listar los adaptadores disponibles"


def test_is_available_distingue_perfiles_utilizables(mini_root):
    reg = _registry(mini_root, fake=FakeProvider("fake"))
    assert reg.is_available("perfil_test") is True
    assert reg.is_available("perfil_sin_adaptador") is False
    assert reg.is_available("perfil_inexistente") is False


# -- política de fallback ------------------------------------------------------
def test_un_error_reintentable_activa_el_fallback_del_perfil(mini_root):
    primario = FakeProvider("fake", fail_with=ProviderError("503", retryable=True))
    respaldo = FakeProvider("fake_alterno")
    reg = _registry(mini_root, fake=primario, fake_alterno=respaldo)

    resp, trace = reg.complete_with_trace(_req("perfil_test"))

    assert resp.model == "fake-respaldo"
    assert trace.used_fallback is True
    assert [a.kind for a in trace.attempts] == ["primary", "profile_fallback"]


def test_un_error_no_reintentable_no_activa_el_fallback(mini_root):
    """Credenciales inválidas no deben ocultarse cambiando de proveedor."""
    primario = FakeProvider("fake", fail_with=ProviderError("401 api key inválida",
                                                           retryable=False, status=401))
    respaldo = FakeProvider("fake_alterno")
    reg = _registry(mini_root, fake=primario, fake_alterno=respaldo)

    with pytest.raises(ProviderError) as exc:
        reg.complete(_req("perfil_test"))

    assert exc.value.status == 401
    assert respaldo.calls == [], "un 401 no debe caer al fallback"


def test_sin_fallback_declarado_el_error_se_propaga(mini_root):
    primario = FakeProvider("fake", fail_with=ProviderError("503", retryable=True))
    reg = _registry(mini_root, fake=primario)

    with pytest.raises(ProviderError):
        reg.complete(_req("perfil_barato"))   # perfil_barato no declara fallback


def test_el_perfil_de_fallback_del_agente_va_despues_del_fallback_del_perfil(mini_root):
    """Orden: primario → fallback de models.yaml → fallback_profile del agente."""
    caido = ProviderError("503", retryable=True)
    primario = FakeProvider("fake", fail_with=caido, fail_times=1)   # falla solo la 1ª vez
    respaldo = FakeProvider("fake_alterno", fail_with=caido)          # el fallback del perfil cae

    reg = _registry(mini_root, fake=primario, fake_alterno=respaldo)
    resp, trace = reg.complete_with_trace(_req("perfil_test"), fallback_profile="perfil_barato")

    assert [a.kind for a in trace.attempts] == ["primary", "profile_fallback", "agent_fallback"]
    assert resp.model == "fake-pequeno"          # el primario del perfil del agente
    assert trace.attempts[-1].profile == "perfil_barato"


def test_los_reintentos_estan_acotados(mini_root):
    primario = FakeProvider("fake", fail_with=ProviderError("timeout", retryable=True))
    respaldo = FakeProvider("fake_alterno")
    reg = _registry(mini_root, retries=2, fake=primario, fake_alterno=respaldo)

    reg.complete(_req("perfil_test"))

    assert len(primario.calls) == 3, "1 intento + 2 reintentos, y ni uno más"
    assert len(respaldo.calls) == 1


def test_un_adaptador_que_no_normaliza_su_error_no_se_reintenta(mini_root):
    """Un error crudo del SDK es un bug, no una indisponibilidad transitoria."""
    class RotoProvider(FakeProvider):
        def complete(self, req, resolved):
            raise ValueError("el SDK explotó de forma inesperada")

    respaldo = FakeProvider("fake_alterno")
    reg = _registry(mini_root, retries=2, fake=RotoProvider("fake"), fake_alterno=respaldo)

    with pytest.raises(ProviderError) as exc:
        reg.complete(_req("perfil_test"))

    assert "no normalizado" in str(exc.value)
    assert respaldo.calls == []


# -- trazabilidad --------------------------------------------------------------
def test_la_traza_permite_auditar_perfil_modelo_y_fallback(mini_root):
    primario = FakeProvider("fake", fail_with=ProviderError("429", retryable=True))
    respaldo = FakeProvider("fake_alterno")
    reg = _registry(mini_root, fake=primario, fake_alterno=respaldo)

    _, trace = reg.complete_with_trace(_req("perfil_test"))
    d = trace.as_dict()

    assert d["perfil_solicitado"] == "perfil_test"
    assert d["proveedor_final"] == "fake_alterno"
    assert d["modelo_final"] == "fake-respaldo"
    assert d["fallback_aplicado"] is True
    assert d["intentos"][0]["error"].startswith("429")
    assert d["intentos"][0]["reintentable"] is True
    assert "latencia_total_ms" in d
