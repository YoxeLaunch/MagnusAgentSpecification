"""Adaptador Google (Gemini) del puerto `LLMProvider`.

Traduce la LLMRequest canónica de Magnus al SDK unificado `google-genai` y
normaliza la respuesta.

Notas de API (SDK google-genai):
- No hay turno "system": el system prompt va en `config.system_instruction`.
- Los roles de turno son "user"/"model", no "user"/"assistant".
- El límite de salida va en `config.max_output_tokens`.
- El "thinking" (Gemini 2.5+) va en `config.thinking_config.thinking_budget`;
  no existe un `effort` nativo, así que se aproxima con el mismo campo.
- Las llamadas a herramienta llegan como `part.function_call` (name + args ya
  como dict, no JSON serializado como en OpenAI) dentro de
  `candidate.content.parts`; Gemini no emite un id de invocación propio, así
  que se usa el nombre de la función como id.
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
    Capability.STREAMING,
    Capability.STRUCTURED_OUTPUT,
}

_FINISH_REASON = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "OTHER": "end_turn",
}


class GoogleProvider(LLMProvider):
    name = "google"

    def __init__(self, client=None, *, timeout_s: float = 120.0):
        # Inyección de dependencia para testear sin red.
        if client is None:
            from google import genai  # dependencia opcional
            client = genai.Client()  # credenciales GOOGLE_API_KEY del entorno
        self._client = client
        self.timeout_s = timeout_s

    def supports(self, cap: Capability) -> bool:
        return cap in _CAPS

    # --- traducción canónico → SDK ----------------------------------------
    def _to_sdk(self, req: LLMRequest, resolved: ResolvedModel) -> dict:
        system_parts = [m.content for m in req.messages if m.role == Role.SYSTEM]
        contents = [
            {"role": "user" if m.role == Role.USER else "model", "parts": [{"text": m.content}]}
            for m in req.messages
            if m.role in (Role.USER, Role.ASSISTANT)
        ]

        config: dict = {"max_output_tokens": req.max_tokens}
        if system_parts:
            config["system_instruction"] = "\n\n".join(system_parts)

        # `thinking`/`effort` son campos CANÓNICOS de Magnus; Gemini solo
        # entiende un presupuesto de tokens de pensamiento, así que ambos se
        # traducen al mismo `thinking_budget` (dinámico con -1).
        extra = dict(resolved.params)
        thinking = req.thinking or extra.pop("thinking", None)
        effort = req.effort or extra.pop("effort", None)
        if thinking == "adaptive" or effort:
            config["thinking_config"] = {"thinking_budget": -1}

        if req.tools:
            config["tools"] = [{"function_declarations": [
                {"name": t.name, "description": t.description, "parameters": t.input_schema}
                for t in req.tools
            ]}]
        if req.stop:
            config["stop_sequences"] = req.stop
        config.update(extra)  # knobs restantes de models.yaml (temperature, top_p…)
        return {"model": resolved.model, "contents": contents, "config": config}

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse:
        payload = self._to_sdk(req, resolved)
        try:
            resp = self._client.models.generate_content(**payload)
        except Exception as e:  # normalizamos a ProviderError
            raise self._translate_error(e) from e

        candidates = getattr(resp, "candidates", None) or []
        candidate = candidates[0] if candidates else None

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_finish = getattr(candidate, "finish_reason", None) if candidate else None
        raw_finish = getattr(raw_finish, "name", raw_finish)  # enum → str si aplica

        if candidate is not None and candidate.content is not None:
            for part in candidate.content.parts or []:
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    tool_calls.append(ToolCall(id=fc.name, name=fc.name, arguments=dict(fc.args or {})))
                elif getattr(part, "text", None):
                    text_parts.append(part.text)

        stop_reason = "tool_use" if tool_calls else _FINISH_REASON.get(raw_finish, "end_turn")

        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
            ) if usage is not None else Usage(),
            model=resolved.model,
        )

    def stream(self, req: LLMRequest, resolved: ResolvedModel) -> Iterator[LLMChunk]:
        payload = self._to_sdk(req, resolved)
        try:
            for chunk in self._client.models.generate_content_stream(**payload):
                text = getattr(chunk, "text", None)
                if text:
                    yield LLMChunk(delta=text, kind="text")
        except Exception as e:
            raise self._translate_error(e) from e

    @staticmethod
    def _translate_error(e: Exception) -> ProviderError:
        status = getattr(e, "code", None) or getattr(e, "status_code", None)
        if status is not None:
            retryable = status in (408, 409, 429) or status >= 500
        else:
            nombre = type(e).__name__
            retryable = "Timeout" in nombre or "Connection" in nombre
        return ProviderError(str(e), retryable=retryable, status=status)
