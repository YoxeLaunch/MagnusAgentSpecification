"""Adaptador OpenAI del puerto `LLMProvider`.

Traduce la LLMRequest canónica de Magnus al SDK oficial `openai` (Chat
Completions) y normaliza la respuesta.

Notas de API (SDK openai):
- `max_completion_tokens` es el nombre vigente del límite de salida; los
  modelos de razonamiento (o-series) rechazan el `max_tokens` legado.
- El esfuerzo de razonamiento va en `reasoning_effort` (low|medium|high), a
  nivel superior del payload — no hay envoltorio tipo `output_config` como en
  Anthropic.
- Los tool calls llegan en `message.tool_calls[i].function.arguments` como
  JSON *serializado en texto*, no como dict — hay que parsearlo.
- Comprobar `finish_reason == "content_filter"` antes de asumir texto válido.
"""
from __future__ import annotations

import json
from typing import Iterator

from .base import (
    Capability,
    LLMChunk,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ResolvedModel,
    Role,
    ToolCall,
    Usage,
)

_CAPS = {
    Capability.TOOLS,
    Capability.VISION,
    Capability.EFFORT,
    Capability.STREAMING,
    Capability.STRUCTURED_OUTPUT,
}

_FINISH_REASON = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, client=None, *, timeout_s: float = 120.0):
        # Inyección de dependencia para testear sin red.
        if client is None:
            import openai  # dependencia opcional
            client = openai.OpenAI(timeout=timeout_s)  # credenciales del entorno
        self._client = client
        self.timeout_s = timeout_s

    def supports(self, cap: Capability) -> bool:
        return cap in _CAPS

    # --- traducción canónico → SDK ----------------------------------------
    def _to_sdk(self, req: LLMRequest, resolved: ResolvedModel) -> dict:
        turns = [
            {"role": m.role.value, "content": m.content}
            for m in req.messages
            if m.role in (Role.SYSTEM, Role.USER, Role.ASSISTANT)
        ]
        payload: dict = {
            "model": resolved.model,
            "max_completion_tokens": req.max_tokens,
            "messages": turns,
        }

        # `effort` es un campo CANÓNICO de Magnus (ver providers/base.py); lo
        # declara el perfil en models.yaml o el agente en la petición. Lo que
        # pide el agente gana sobre el valor del perfil.
        extra = dict(resolved.params)
        effort = req.effort or extra.pop("effort", None)
        extra.pop("thinking", None)  # sin equivalente en OpenAI; se descarta, no se vuelca crudo
        if effort:
            payload["reasoning_effort"] = effort

        if req.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in req.tools
            ]
        if req.stop:
            payload["stop"] = req.stop
        payload.update(extra)  # knobs restantes de models.yaml (temperature, top_p…)
        return payload

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse:
        payload = self._to_sdk(req, resolved)
        try:
            resp = self._client.chat.completions.create(**payload)
        except Exception as e:  # normalizamos a ProviderError
            raise self._translate_error(e) from e

        choice = resp.choices[0]
        finish_reason = _FINISH_REASON.get(choice.finish_reason, choice.finish_reason)
        if finish_reason == "refusal":
            return LLMResponse(text="", stop_reason="refusal", model=resp.model)

        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments))
            for tc in (choice.message.tool_calls or [])
        ]

        return LLMResponse(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason=finish_reason,
            usage=Usage(
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
                cache_read_tokens=getattr(
                    getattr(resp.usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
            ),
            model=resp.model,
        )

    def stream(self, req: LLMRequest, resolved: ResolvedModel) -> Iterator[LLMChunk]:
        payload = self._to_sdk(req, resolved)
        try:
            for chunk in self._client.chat.completions.create(stream=True, **payload):
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    yield LLMChunk(delta=delta.content, kind="text")
        except Exception as e:
            raise self._translate_error(e) from e

    @staticmethod
    def _translate_error(e: Exception) -> ProviderError:
        status = getattr(e, "status_code", None)
        if status is not None:
            # 401/403 (credenciales) y 400 (petición inválida) NO son reintentables:
            # el registro debe fallar claro, no cambiar de proveedor (ver la
            # política en providers/registry.py).
            retryable = status in (408, 409, 429) or status >= 500
        else:
            # Sin status: timeout o corte de conexión. Son transitorios y sí
            # autorizan reintento/fallback.
            nombre = type(e).__name__
            retryable = "Timeout" in nombre or "Connection" in nombre
        return ProviderError(str(e), retryable=retryable, status=status)
