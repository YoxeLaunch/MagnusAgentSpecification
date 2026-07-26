"""Adaptador Anthropic del puerto `LLMProvider`.

Traduce la LLMRequest canónica de Magnus al SDK oficial `anthropic` y normaliza
la respuesta. Modelo por defecto: claude-opus-4-8 (perfil reasoning_high).

Notas de API (SDK anthropic):
- Adaptive thinking: thinking={"type": "adaptive"}; en Opus 4.8/4.7 hay que
  activarlo explícitamente (no está on por omisión). `budget_tokens` da 400.
- Effort va en output_config={"effort": ...}, NO a nivel superior.
- Para max_tokens grande, usar streaming para evitar timeouts HTTP.
- Comprobar stop_reason == "refusal" antes de leer content.
"""
from __future__ import annotations

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
    Capability.THINKING,
    Capability.VISION,
    Capability.EFFORT,
    Capability.STREAMING,
    Capability.STRUCTURED_OUTPUT,
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, client=None, *, timeout_s: float = 120.0):
        # Inyección de dependencia para testear sin red.
        if client is None:
            import anthropic  # dependencia opcional
            # Timeout explícito: sin él una petición colgada bloquea la
            # consulta entera y nunca llega a activarse el fallback.
            client = anthropic.Anthropic(timeout=timeout_s)  # credenciales del entorno
        self._client = client
        self.timeout_s = timeout_s

    def supports(self, cap: Capability) -> bool:
        return cap in _CAPS

    # --- traducción canónico → SDK ----------------------------------------
    def _to_sdk(self, req: LLMRequest, resolved: ResolvedModel) -> dict:
        system_parts = [m.content for m in req.messages if m.role == Role.SYSTEM]
        turns = [
            {"role": m.role.value, "content": m.content}
            for m in req.messages
            if m.role in (Role.USER, Role.ASSISTANT)
        ]
        payload: dict = {
            "model": resolved.model,
            "max_tokens": req.max_tokens,
            "messages": turns,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        # `thinking` y `effort` son campos CANÓNICOS de Magnus, no knobs crudos
        # del SDK: models.yaml los declara dentro del perfil (p. ej.
        # `{provider: anthropic, model: ..., thinking: adaptive, effort: high}`)
        # y hay que traducirlos igual que si vinieran en la LLMRequest. Volcarlos
        # tal cual mandaría `thinking: "adaptive"` (string, no objeto) y `effort`
        # a nivel superior — justo lo que este adaptador documenta que NO se hace.
        # Lo que declara el agente en la petición gana sobre el valor del perfil.
        extra = dict(resolved.params)
        thinking = req.thinking or extra.pop("thinking", None)
        effort = req.effort or extra.pop("effort", None)
        if thinking == "adaptive":
            payload["thinking"] = {"type": "adaptive"}
        if effort:
            payload["output_config"] = {"effort": effort}

        if req.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in req.tools
            ]
        if req.stop:
            payload["stop_sequences"] = req.stop
        payload.update(extra)  # knobs restantes de models.yaml (temperature, top_p…)
        return payload

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse:
        payload = self._to_sdk(req, resolved)
        try:
            resp = self._client.messages.create(**payload)
        except Exception as e:  # normalizamos a ProviderError
            raise self._translate_error(e) from e

        if resp.stop_reason == "refusal":
            return LLMResponse(text="", stop_reason="refusal", model=resp.model,
                               raw={"stop_details": getattr(resp, "stop_details", None)})

        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            ),
            model=resp.model,
        )

    def stream(self, req: LLMRequest, resolved: ResolvedModel) -> Iterator[LLMChunk]:
        payload = self._to_sdk(req, resolved)
        try:
            with self._client.messages.stream(**payload) as s:
                for event in s:
                    if event.type == "content_block_delta":
                        d = event.delta
                        if d.type == "text_delta":
                            yield LLMChunk(delta=d.text, kind="text")
                        elif d.type == "thinking_delta":
                            yield LLMChunk(delta=d.thinking, kind="thinking")
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
            # autorizan reintento/fallback; antes caían en retryable=False y
            # abortaban la consulta sin darle oportunidad al respaldo.
            nombre = type(e).__name__
            retryable = "Timeout" in nombre or "Connection" in nombre
        return ProviderError(str(e), retryable=retryable, status=status)
