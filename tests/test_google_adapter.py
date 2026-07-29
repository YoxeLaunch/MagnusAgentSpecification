"""Traducción canónico → SDK del adaptador Google (Gemini), con cliente inyectado.

No requiere el paquete `google-genai` ni credenciales: `GoogleProvider` acepta
un cliente por inyección justo para esto.
"""
from __future__ import annotations

import pytest

from providers.base import LLMRequest, Message, ProviderError, ResolvedModel, Role, ToolCall
from providers.google_provider import GoogleProvider


class _Parte:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class _LlamadaFuncion:
    def __init__(self, name, args): self.name, self.args = name, args


class _Contenido:
    def __init__(self, parts): self.parts = parts


class _FinishReason:
    def __init__(self, name): self.name = name


class _Candidato:
    def __init__(self, parts, finish_reason="STOP"):
        self.content = _Contenido(parts)
        self.finish_reason = _FinishReason(finish_reason)


class _Uso:
    prompt_token_count, candidates_token_count, cached_content_token_count = 12, 34, 0


class _Respuesta:
    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [_Candidato([_Parte(text="hola")])]
        self.usage_metadata = _Uso()


class _ClienteStub:
    """Captura el payload que el adaptador manda al SDK."""

    def __init__(self, error: Exception | None = None, respuesta: _Respuesta | None = None):
        self.payloads: list[dict] = []
        self._error = error
        self._respuesta = respuesta or _Respuesta()
        self.models = self

    def generate_content(self, **payload):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        return self._respuesta


def _req(**kw) -> LLMRequest:
    kw.setdefault("messages", [Message(Role.SYSTEM, "sistema"), Message(Role.USER, "pregunta")])
    kw.setdefault("profile", "reasoning_high")
    return LLMRequest(**kw)


def test_el_system_va_en_system_instruction_no_como_turno():
    cliente = _ClienteStub()
    GoogleProvider(cliente).complete(_req(), ResolvedModel("google", "gemini-2.5-pro"))

    p = cliente.payloads[0]
    assert p["config"]["system_instruction"] == "sistema"
    assert p["contents"] == [{"role": "user", "parts": [{"text": "pregunta"}]}]
    assert p["model"] == "gemini-2.5-pro"


def test_assistant_se_traduce_a_rol_model():
    cliente = _ClienteStub()
    req = _req(messages=[Message(Role.USER, "hola"), Message(Role.ASSISTANT, "hola de vuelta")])
    GoogleProvider(cliente).complete(req, ResolvedModel("google", "gemini-2.5-pro"))

    assert cliente.payloads[0]["contents"] == [
        {"role": "user", "parts": [{"text": "hola"}]},
        {"role": "model", "parts": [{"text": "hola de vuelta"}]},
    ]


def test_thinking_adaptive_se_traduce_a_thinking_budget_dinamico():
    cliente = _ClienteStub()
    resolved = ResolvedModel("google", "gemini-2.5-pro", params={"thinking": "adaptive"})
    GoogleProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["config"]["thinking_config"] == {"thinking_budget": -1}


def test_effort_se_aproxima_con_el_mismo_thinking_budget():
    """Gemini no tiene un `effort` nativo; se traduce al único knob equivalente."""
    cliente = _ClienteStub()
    resolved = ResolvedModel("google", "gemini-2.5-pro", params={"effort": "high"})
    GoogleProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["config"]["thinking_config"] == {"thinking_budget": -1}
    assert "effort" not in cliente.payloads[0]["config"], "no debe volcarse crudo, Gemini lo rechazaría"


def test_los_knobs_restantes_del_perfil_pasan_al_config():
    cliente = _ClienteStub()
    resolved = ResolvedModel("google", "gemini-2.5-pro", params={"temperature": 0.2})
    GoogleProvider(cliente).complete(_req(), resolved)

    assert cliente.payloads[0]["config"]["temperature"] == 0.2


def test_las_function_call_se_extraen_de_las_parts():
    respuesta = _Respuesta(candidates=[_Candidato(
        [_Parte(function_call=_LlamadaFuncion("buscar", {"query": "x"}))])])
    cliente = _ClienteStub(respuesta=respuesta)

    resp = GoogleProvider(cliente).complete(_req(), ResolvedModel("google", "gemini-2.5-pro"))

    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls == [ToolCall(id="buscar", name="buscar", arguments={"query": "x"})]


def test_safety_se_normaliza_a_refusal():
    respuesta = _Respuesta(candidates=[_Candidato([_Parte(text="")], finish_reason="SAFETY")])
    cliente = _ClienteStub(respuesta=respuesta)

    resp = GoogleProvider(cliente).complete(_req(), ResolvedModel("google", "gemini-2.5-pro"))

    assert resp.stop_reason == "refusal"


def test_el_modelo_real_reportado_es_el_resuelto():
    """Gemini no devuelve el modelo real en la respuesta, a diferencia de Anthropic/OpenAI."""
    cliente = _ClienteStub()
    resp = GoogleProvider(cliente).complete(_req(), ResolvedModel("google", "gemini-2.5-flash"))

    assert resp.model == "gemini-2.5-flash"


# -- normalización de errores --------------------------------------------------
class _ErrorAPI(Exception):
    def __init__(self, code): self.code = code


class APITimeoutError(Exception):
    pass


@pytest.mark.parametrize("status,reintentable", [
    (401, False), (403, False), (400, False),
    (429, True), (500, True), (503, True), (408, True),
])
def test_normaliza_los_codigos_de_error(status, reintentable):
    provider = GoogleProvider(_ClienteStub(error=_ErrorAPI(status)))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("google", "gemini-2.5-pro"))

    assert exc.value.status == status
    assert exc.value.retryable is reintentable


def test_un_timeout_sin_status_es_reintentable():
    provider = GoogleProvider(_ClienteStub(error=APITimeoutError("se agotó el tiempo")))

    with pytest.raises(ProviderError) as exc:
        provider.complete(_req(), ResolvedModel("google", "gemini-2.5-pro"))

    assert exc.value.retryable is True
