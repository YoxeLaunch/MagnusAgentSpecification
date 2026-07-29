"""Traducción canónico → SDK del adaptador OpenAI, con cliente inyectado.

No requiere el paquete `openai` ni credenciales: `OpenAIProvider` acepta un
cliente por inyección justo para esto.
"""
from __future__ import annotations

import json

import pytest

from providers.base import LLMRequest, Message, ProviderError, ResolvedModel, Role, ToolCall
from providers.openai_provider import OpenAIProvider


class _FuncionLlamada:
    def __init__(self, name, arguments): self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FuncionLlamada(name, arguments)


class _Mensaje:
    def __init__(self, content="hola", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message=None, finish_reason="stop"):
        self.message = message or _Mensaje()
        self.finish_reason = finish_reason


class _Uso:
    prompt_tokens, completion_tokens = 12, 34
    prompt_tokens_details = None


class _Respuesta:
    def __init__(self, choices=None, model="gpt-5"):
        self.choices = choices or [_Choice()]
        self.usage = _Uso()
        self.model = model


class _ClienteStub:
    """Captura el payload que el adaptador manda al SDK."""

    def __init__(self, error: Exception | None = None, respuesta: _Respuesta | None = None):
        self.payloads: list[dict] = []
        self._error = error
        self._respuesta = respuesta or _Respuesta()
        self.chat = self
        self.completions = self

    def create(self, **payload):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        return self._respuesta


def _req(**kw) -> LLMRequest:
    kw.setdefault("messages", [Message(Role.SYSTEM, "sistema"), Message(Role.USER, "pregunta")])
    kw.setdefault("profile", "reasoning_high")
    return LLMRequest(**kw)


def test_traduce_system_y_turnos():
    cliente = _ClienteStub()
    OpenAIProvider(cliente).complete(_req(), ResolvedModel("openai", "gpt-5"))

    p = cliente.payloads[0]
    assert p["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "pregunta"},
    ]
    assert p["model"] == "gpt-5"
    assert p["max_completion_tokens"] == 16_000


def test_el_effort_del_perfil_se_traduce_a_reasoning_effort():
    cliente = _ClienteStub()
    resolved = ResolvedModel("openai", "o3", params={"effort": "high"})
    OpenAIProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["reasoning_effort"] == "high"


def test_el_effort_del_agente_gana_sobre_el_del_perfil():
    cliente = _ClienteStub()
    resolved = ResolvedModel("openai", "o3", params={"effort": "high"})
    OpenAIProvider(cliente).complete(_req(effort="low"), resolved)

    assert cliente.payloads[0]["reasoning_effort"] == "low"


def test_thinking_no_tiene_equivalente_y_se_descarta_sin_volcarse_crudo():
    cliente = _ClienteStub()
    resolved = ResolvedModel("openai", "gpt-5", params={"thinking": "adaptive"})
    OpenAIProvider(cliente).complete(_req(), resolved)

    assert "thinking" not in cliente.payloads[0]


def test_los_knobs_restantes_del_perfil_pasan_al_payload():
    cliente = _ClienteStub()
    resolved = ResolvedModel("openai", "gpt-5", params={"temperature": 0.2})
    OpenAIProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["temperature"] == 0.2


def test_las_tool_calls_se_parsean_desde_json_serializado():
    tool_calls = [_ToolCall("call_1", "buscar", json.dumps({"query": "x"}))]
    respuesta = _Respuesta(choices=[_Choice(_Mensaje(content=None, tool_calls=tool_calls),
                                             finish_reason="tool_calls")])
    cliente = _ClienteStub(respuesta=respuesta)

    resp = OpenAIProvider(cliente).complete(_req(), ResolvedModel("openai", "gpt-5"))

    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls == [ToolCall(id="call_1", name="buscar", arguments={"query": "x"})]


def test_content_filter_se_normaliza_a_refusal():
    respuesta = _Respuesta(choices=[_Choice(finish_reason="content_filter")])
    cliente = _ClienteStub(respuesta=respuesta)

    resp = OpenAIProvider(cliente).complete(_req(), ResolvedModel("openai", "gpt-5"))

    assert resp.stop_reason == "refusal"
    assert resp.text == ""


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
    provider = OpenAIProvider(_ClienteStub(error=_ErrorHTTP(status)))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("openai", "gpt-5"))

    assert exc.value.status == status
    assert exc.value.retryable is reintentable


def test_un_timeout_sin_status_es_reintentable():
    provider = OpenAIProvider(_ClienteStub(error=APITimeoutError("se agotó el tiempo")))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("openai", "gpt-5"))

    assert exc.value.retryable is True
