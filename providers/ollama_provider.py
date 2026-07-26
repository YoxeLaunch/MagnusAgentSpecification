"""Adaptador Ollama (local, gratis) del puerto LLMProvider — solo stdlib.

Cuando instales Ollama (https://ollama.com) y descargues un modelo
(p.ej. `ollama pull qwen2.5:7b`), este adaptador genera prosa sin coste ni nube.
Se selecciona vía configs/models.yaml (profile → provider: ollama).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from providers.base import (
    Capability, LLMProvider, LLMRequest, LLMResponse, ProviderError, ResolvedModel, Role, Usage,
)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", *, timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def supports(self, cap: Capability) -> bool:
        return cap in {Capability.STREAMING}

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse:
        messages = [{"role": m.role.value, "content": m.content} for m in req.messages
                    if m.role in (Role.SYSTEM, Role.USER, Role.ASSISTANT)]
        opciones = {"num_predict": req.max_tokens}
        # knobs del perfil (models.yaml): temperature, top_p, num_ctx…
        opciones.update({k: v for k, v in resolved.params.items()
                         if k not in ("thinking", "effort")})
        payload = {
            "model": resolved.model,
            "messages": messages,
            "stream": False,
            "options": opciones,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # 4xx del servidor local (modelo inexistente, petición inválida) no
            # son transitorios: reintentar o cambiar de proveedor no los arregla.
            retryable = e.code in (408, 429) or e.code >= 500
            raise ProviderError(f"Ollama respondió {e.code}: {e.reason}",
                                retryable=retryable, status=e.code) from e
        except Exception as e:
            raise ProviderError(f"Ollama no disponible: {e}", retryable=True) from e

        text = body.get("message", {}).get("content", "")
        return LLMResponse(
            text=text, stop_reason="end_turn", model=resolved.model,
            usage=Usage(input_tokens=body.get("prompt_eval_count", 0),
                        output_tokens=body.get("eval_count", 0)))

    def stream(self, req: LLMRequest, resolved: ResolvedModel):
        # Simplificado: reusar complete() y emitir un único chunk.
        yield from ()  # placeholder — implementar SSE de Ollama si se necesita
