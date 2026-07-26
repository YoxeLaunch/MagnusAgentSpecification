"""Puerto MemoryEngine — memoria desacoplada del agente.

Ver docs/04-MAGNUS-V2-ARQUITECTURA.md §9 y docs/02-COMPONENTES.md componente 5.
Ningún agente implementa su propio almacenamiento: todos hablan con el mismo
`MemoryEngine` a través de `MemoryScope`. Cinco particiones:

  short_term     — turno/sesión activa
  long_term      — preferencias/hechos de usuario, persistentes
  episodic       — traza de qué pasó (decisiones, respuestas dadas)
  semantic       — conocimiento "aprendido" PROPUESTO (nunca escrito directo
                   a knowledge/ — P7 de la constitución: humano aprueba)
  project        — Project Memory: seguimiento de un hilo/proyecto multi-sesión
                   (partición nueva respecto al componente 5 original)

Esta es una implementación de referencia (SQLite, stdlib) — el contrato es el
que importa: mañana se sustituye por Redis+Postgres+Qdrant sin tocar quien lo
consume (igual que LLMProvider/RAGPipeline).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROJECT = "project"


@dataclass(frozen=True)
class MemoryScope:
    type: MemoryType
    agent_id: str
    user_id: str
    project_id: str | None = None   # obligatorio para type=PROJECT
    session_id: str | None = None   # obligatorio para type=SHORT_TERM


@dataclass(frozen=True)
class MemoryItem:
    text: str
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class SemanticFact:
    text: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass(frozen=True)
class ConsolidationReport:
    session_id: str
    items_consolidated: int
    summary: str = ""


class MemoryEngine(Protocol):
    def remember(self, scope: MemoryScope, item: MemoryItem) -> None: ...
    def recall(self, scope: MemoryScope, query: str, k: int = 5) -> list[MemoryItem]: ...
    def consolidate(self, session_id: str) -> ConsolidationReport: ...
    def propose_semantic(self, agent_id: str, fact: SemanticFact) -> str: ...   # → proposal_id
    def list_proposals(self, *, status: str | None = None) -> list[dict]: ...
    def approve_proposal(self, proposal_id: str, reviewer: str) -> None: ...
    def reject_proposal(self, proposal_id: str, reviewer: str, reason: str) -> None: ...
