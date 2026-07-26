"""Router Inteligente — el 'cerebro' de Magnus.

Detecta intención(es), selecciona agentes por índice semántico y decide el modo
(single / parallel / sequential). Al final fusiona las respuestas en un informe
único y coherente. Es STATELESS → escalable horizontalmente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RouteMode(str, Enum):
    SINGLE = "single"
    PARALLEL = "parallel"      # fan-out (varios agentes en paralelo)
    SEQUENTIAL = "sequential"  # pipeline (salida de uno alimenta al siguiente)


@dataclass(frozen=True)
class UserRequest:
    request_id: str
    message: str
    user_id: str
    forced_agent: str | None = None


@dataclass(frozen=True)
class AgentMatch:
    agent_id: str
    score: float


@dataclass(frozen=True)
class RoutePlan:
    agents: list[str]
    mode: RouteMode
    reason: str = ""


@dataclass(frozen=True)
class AgentAnswer:
    agent_id: str
    text: str
    citations: list[dict] = field(default_factory=list)
    score: float | None = None


@dataclass(frozen=True)
class FinalAnswer:
    text: str
    contributors: list[str]
    citations: list[dict] = field(default_factory=list)


class Router:
    """Depende de puertos: registry (búsqueda de agentes) y provider (LLM)."""

    def __init__(self, registry, provider, *, max_agents: int = 3, min_score: float = 0.35):
        self._registry = registry     # AgentRegistry.search(intent, k)
        self._provider = provider     # ProviderRegistry.complete(...)
        self._max_agents = max_agents
        self._min_score = min_score

    # 1) intención → agentes → plan --------------------------------------
    def route(self, request: UserRequest) -> RoutePlan:
        if request.forced_agent:
            return RoutePlan([request.forced_agent], RouteMode.SINGLE, "forzado por el usuario")

        matches: list[AgentMatch] = self._registry.search(request.message, k=self._max_agents)
        chosen = [m.agent_id for m in matches if m.score >= self._min_score]

        if not chosen:
            # sin match claro → agente generalista de respaldo
            return RoutePlan(["generalist"], RouteMode.SINGLE, "sin dominio claro")
        if len(chosen) == 1:
            return RoutePlan(chosen, RouteMode.SINGLE, "un único dominio detectado")
        # varios dominios → colaboración en paralelo y fusión posterior
        return RoutePlan(chosen, RouteMode.PARALLEL,
                         f"múltiples dominios: {', '.join(chosen)}")

    # 2) fusión de respuestas → informe único ----------------------------
    def merge(self, request: UserRequest, answers: list[AgentAnswer]) -> FinalAnswer:
        if len(answers) == 1:
            a = answers[0]
            return FinalAnswer(a.text, [a.agent_id], a.citations)

        from providers.base import LLMRequest, Message, Role
        blocks = "\n\n".join(f"### {a.agent_id}\n{a.text}" for a in answers)
        merge_prompt = (
            "Fusiona las siguientes respuestas de agentes especializados en un único "
            "informe coherente para el usuario. Preserva las citas, evita contradicciones "
            "y organiza por temas. No inventes información no presente.\n\n"
            f"Pregunta original: {request.message}\n\n{blocks}"
        )
        resp = self._provider.complete(LLMRequest(
            messages=[
                Message(Role.SYSTEM, "Eres el editor jefe de Magnus Dynamic Group."),
                Message(Role.USER, merge_prompt),
            ],
            profile="reasoning_std",
        ))
        citations = [c for a in answers for c in a.citations]
        return FinalAnswer(resp.text, [a.agent_id for a in answers], citations)
