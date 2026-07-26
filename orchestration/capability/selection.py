"""Agent Selection Engine — responde "¿quién atiende esta capacidad?".

Separación deliberada de `matcher.py` (a pedido explícito, antes de tocar
embeddings): este módulo NUNCA analiza texto de la consulta del usuario.
Solo conoce `AgentRegistry` y una lista de `CapabilityMatch` ya resueltos
por un `CapabilityMatcher`. Esto significa que cambiar CÓMO se decide "de
qué trata la consulta" (léxico → embeddings) no toca ni una línea de cómo
se decide "quién la atiende" — son dos preguntas, dos componentes, dos
puntos de extensión independientes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from orchestration.registry.agent_registry import AgentRegistry, AgentSpec
from .matcher import CapabilityMatch

_STRENGTH_WEIGHT = {"primary": 1.0, "secondary": 0.6}


@dataclass(frozen=True)
class AgentSelection:
    agent: AgentSpec
    score: float
    matched_capabilities: list[str] = field(default_factory=list)


class AgentSelectionEngine:
    def __init__(self, registry: AgentRegistry):
        self._registry = registry

    def agents_for(self, capability_id: str) -> list[AgentSpec]:
        return self._registry.agents_for_capability(capability_id)

    def select(self, capability_matches: list[CapabilityMatch], *, k: int = 3) -> list[AgentSelection]:
        scored: dict[str, tuple[float, list[str]]] = {}
        agent_by_id: dict[str, AgentSpec] = {}

        for cm in capability_matches:
            for agent in self.agents_for(cm.capability_id):
                strength = next(
                    (c.strength for c in agent.capabilities if c.id == cm.capability_id),
                    "secondary",
                )
                weight = _STRENGTH_WEIGHT.get(strength, 0.5)
                prev_score, prev_caps = scored.get(agent.id, (0.0, []))
                scored[agent.id] = (prev_score + cm.score * weight, prev_caps + [cm.capability_id])
                agent_by_id[agent.id] = agent

        ranked = sorted(
            scored.items(),
            key=lambda kv: (kv[1][0], agent_by_id[kv[0]].priority),
            reverse=True,
        )
        return [AgentSelection(agent_by_id[aid], round(score, 3), caps)
                for aid, (score, caps) in ranked[:k]]
