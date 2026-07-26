"""Traducción canónico → SDK del adaptador Anthropic, con cliente inyectado.

No requiere el paquete `anthropic` ni credenciales: `AnthropicProvider` acepta
un cliente por inyección justo para esto.
"""
from __future__ import annotations

import pytest

from providers.anthropic_provider import AnthropicProvider
from providers.base import LLMRequest, Message, ProviderError, ResolvedModel, Role


class _Bloque:
    def __init__(self, text): self.type, self.text = "text", text


class _Uso:
    input_tokens, output_tokens, cache_read_input_tokens = 12, 34, 0


class _Respuesta:
    stop_reason, model = "end_turn", "claude-sonnet-5"
    content = [_Bloque("hola")]
    usage = _Uso()


class _ClienteStub:
    """Captura el payload que el adaptador manda al SDK."""

    def __init__(self, error: Exception | None = None):
        self.payloads: list[dict] = []
        self._error = error
        self.messages = self

    def create(self, **payload):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        return _Respuesta()


def _req(**kw) -> LLMRequest:
    kw.setdefault("messages", [Message(Role.SYSTEM, "sistema"), Message(Role.USER, "pregunta")])
    kw.setdefault("profile", "reasoning_high")
    return LLMRequest(**kw)


def test_traduce_system_y_turnos():
    cliente = _ClienteStub()
    AnthropicProvider(cliente).complete(_req(), ResolvedModel("anthropic", "claude-sonnet-5"))

    p = cliente.payloads[0]
    assert p["system"] == "sistema"
    assert p["messages"] == [{"role": "user", "content": "pregunta"}]
    assert p["model"] == "claude-sonnet-5"


def test_thinking_y_effort_del_perfil_se_traducen_no_se_vuelcan_crudos():
    """`models.yaml` los declara dentro de `primary`; son campos canónicos."""
    cliente = _ClienteStub()
    resolved = ResolvedModel("anthropic", "claude-opus-4-8",
                             params={"thinking": "adaptive", "effort": "high"})
    AnthropicProvider(cliente).complete(_req(), resolved)

    p = cliente.payloads[0]
    assert p["thinking"] == {"type": "adaptive"}, "objeto, no el string 'adaptive'"
    assert p["output_config"] == {"effort": "high"}, "effort va en output_config"
    assert "effort" not in p, "no debe quedar a nivel superior del payload"


def test_el_effort_del_agente_gana_sobre_el_del_perfil():
    cliente = _ClienteStub()
    resolved = ResolvedModel("anthropic", "claude-opus-4-8", params={"effort": "high"})
    AnthropicProvider(cliente).complete(_req(effort="low"), resolved)

    assert cliente.payloads[0]["output_config"] == {"effort": "low"}


def test_los_knobs_restantes_del_perfil_pasan_al_payload():
    cliente = _ClienteStub()
    resolved = ResolvedModel("anthropic", "claude-sonnet-5", params={"temperature": 0.2})
    AnthropicProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["temperature"] == 0.2


# -- normalización de errores --------------------------------------------------
class _ErrorHTTP(Exception):
    def __init__(self, status): self.status_code = status


class APITimeoutError(Exception):
    pass


@pytest.mark.parametrize("status,reintentable", [
    (401, False), (403, False), (400, False),
    (429, True), (500, True), (503, True), (408, True),
])
def test_normaliza_los_codigos_http(status, reintentable):
    provider = AnthropicProvider(_ClienteStub(error=_ErrorHTTP(status)))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("anthropic", "claude-sonnet-5"))

    assert exc.value.status == status
    assert exc.value.retryable is reintentable


def test_un_timeout_sin_status_es_reintentable():
    """Antes caía en retryable=False y abortaba sin darle turno al fallback."""
    provider = AnthropicProvider(_ClienteStub(error=APITimeoutError("se agotó el tiempo")))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("anthropic", "claude-sonnet-5"))

    assert exc.value.retryable is True
