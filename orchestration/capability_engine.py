"""CapabilityEngine — fachada delgada que compone dos componentes separados.

  query → CapabilityMatcher (orchestration/capability/matcher.py)
        → capacidades con score
        → AgentSelectionEngine (orchestration/capability/selection.py)
        → agentes rankeados

La separación entre "¿de qué trata esto?" y "¿quién lo atiende?" vive en
esos dos módulos, cada uno con su propia responsabilidad y su propio punto
de extensión (ver sus docstrings). Este archivo solo los compone y mantiene
la API pública estable que ya consumen `orchestration/engine.py` y
`sdk/cli.py`, para no forzar cambios río abajo cuando el matcher cambie
(p. ej. a embeddings) o cuando la selección de agentes gane más señales
(carga, coste, disponibilidad).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .registry.agent_registry import AgentRegistry, AgentSpec
from .registry.capability_catalog import CapabilityCatalog
from .capability.matcher import (
    CapabilityMatch, CapabilityMatcher, HybridCapabilityMatcher, HybridChannelScores,
    LexicalCapabilityMatcher,
)
from .capability.selection import AgentSelectionEngine


@dataclass(frozen=True)
class MatchExplanation:
    """Explicación auditable de por qué (no) se enrutó a un agente.

    `channel_scores` solo se rellena cuando el matcher activo sabe desglosar
    por canal (hoy: `HybridCapabilityMatcher.explain_detailed`) — cada
    entrada trae capacidad candidata, score léxico, score vectorial, score
    final, motivo de selección y umbral aplicado, que es exactamente lo que
    pide auditar el paso 6 del ROADMAP. Con un matcher que no lo soporte
    (p. ej. `LexicalCapabilityMatcher` a secas) queda vacío y `reason` sigue
    siendo la única explicación disponible, como antes.
    """
    query: str
    agent_id: str
    threshold: float = 0.35
    capability_scores: list[CapabilityMatch] = field(default_factory=list)
    channel_scores: list[HybridChannelScores] = field(default_factory=list)
    agent_score: float = 0.0
    reason: str = ""


class CapabilityEngine:
    """`matcher=None` (por defecto) usa `HybridCapabilityMatcher` desde el
    paso 6 del ROADMAP (léxico + vectorial local, nunca embeddings
    neuronales) — antes era `LexicalCapabilityMatcher` a secas. El cambio de
    default se hizo solo tras medir, en `evaluation/bench_routing.py`, que el
    híbrido no empeora ninguna consulta del banco frente al léxico solo. Pasa
    `matcher=LexicalCapabilityMatcher(catalog)` explícitamente si quieres el
    comportamiento anterior (p. ej. para medir el baseline)."""

    def __init__(self, catalog: CapabilityCatalog, registry: AgentRegistry,
                 matcher: CapabilityMatcher | None = None):
        self._catalog = catalog
        self._registry = registry
        self._matcher = matcher or HybridCapabilityMatcher(catalog)
        self._selection = AgentSelectionEngine(registry)

    # -- paso 1: query → capacidades (delegado al matcher) --------------------
    def match(self, query: str, *, k: int = 5, min_score: float = 0.35) -> list[CapabilityMatch]:
        return self._matcher.match(query, k=k, min_score=min_score)

    # -- paso 2: capacidad → agentes (delegado a la selección) ----------------
    def agents_for(self, capability_id: str) -> list[AgentSpec]:
        return self._selection.agents_for(capability_id)

    # -- combinación: query → agentes rankeados --------------------------------
    def route_to_agents(self, query: str, *, k: int = 3,
                         min_score: float = 0.35) -> list[AgentSpec]:
        cap_matches = self._matcher.match(query, k=k * 2, min_score=min_score)
        selections = self._selection.select(cap_matches, k=k)
        return [s.agent for s in selections]

    # -- explicabilidad (auditable, no caja negra) ------------------------------
    def explain(self, query: str, agent_id: str, *, min_score: float = 0.35) -> MatchExplanation:
        agent = self._registry.get(agent_id)
        agent_cap_ids = {c.id for c in agent.capabilities}
        cap_matches = self._matcher.match(query, k=50, min_score=0.0)
        relevant = [m for m in cap_matches if m.capability_id in agent_cap_ids]

        selections = self._selection.select(cap_matches, k=len(self._registry.list()))
        agent_score = next((s.score for s in selections if s.agent.id == agent_id), 0.0)

        reason = (
            f"capacidades del agente con match: {[(m.capability_id, m.via) for m in relevant]}"
            if relevant else "ninguna capacidad del agente matchea la query"
        )

        # Desglose por canal (léxico/vectorial/final/motivo/umbral) si el
        # matcher activo lo soporta — hoy, HybridCapabilityMatcher.
        channel_scores: list[HybridChannelScores] = []
        detailed = getattr(self._matcher, "explain_detailed", None)
        if callable(detailed):
            for cap_id in sorted(agent_cap_ids):
                detalle = detailed(query, cap_id, min_score=min_score)
                if detalle is not None:
                    channel_scores.append(detalle)

        return MatchExplanation(query, agent_id, min_score, relevant, channel_scores,
                                agent_score, reason)
