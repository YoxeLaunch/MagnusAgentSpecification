"""Puerto canónico `LLMProvider` — el modelo de IA es intercambiable.

Todo el resto de Magnus depende SOLO de estas abstracciones, nunca de un SDK
concreto (Clean/Hexagonal Architecture). Los adaptadores (anthropic, openai,
google, mistral, ollama, openrouter) implementan `LLMProvider`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Protocol, runtime_checkable


class Capability(str, Enum):
    TOOLS = "tools"
    THINKING = "thinking"
    VISION = "vision"
    EFFORT = "effort"
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    # bloques tool_use / tool_result normalizados van en `meta` cuando aplica
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict  # JSON Schema


@dataclass(frozen=True)
class LLMRequest:
    """Petición CANÓNICA de Magnus, independiente del proveedor.

    `profile` se resuelve en configs/models.yaml → (provider, model, params).
    Los agentes MAS declaran `model.profile`, nunca un modelo concreto.
    """
    messages: list[Message]
    profile: str                       # p.ej. "reasoning_high"
    max_tokens: int = 16_000
    effort: str | None = None          # low|medium|high|xhigh|max (si aplica)
    thinking: str | None = None        # "adaptive" | None
    tools: list[ToolSpec] = field(default_factory=list)
    stream: bool = False
    stop: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"      # end_turn|tool_use|max_tokens|refusal|...
    usage: Usage = field(default_factory=Usage)
    model: str = ""                    # modelo REAL que respondió (trazabilidad)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LLMChunk:
    delta: str
    kind: str = "text"                 # text | thinking | tool


@dataclass(frozen=True)
class ResolvedModel:
    """Salida de resolver un `profile` contra models.yaml."""
    provider: str
    model: str
    params: dict = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse: ...
    def stream(self, req: LLMRequest, resolved: ResolvedModel) -> Iterator[LLMChunk]: ...
    def supports(self, cap: Capability) -> bool: ...


@runtime_checkable
class Embedder(Protocol):
    """Puerto de embeddings — también intercambiable (bge-m3, voyage, openai...)."""
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ProviderError(RuntimeError):
    """Error normalizado; los adaptadores traducen los errores de su SDK aquí."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
