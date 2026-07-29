"""Implementación de referencia de MemoryEngine sobre SQLite (stdlib).

No es la implementación de producción (docs/02-COMPONENTES.md componente 5
propone Redis + Postgres + Qdrant) — es el mismo contrato, para poder
desarrollar y probar sin infraestructura externa. Sustituir esta clase por
una respaldada en Redis/Postgres no debe requerir cambios en quien consume
`MemoryEngine`.

`semantic` nunca se aplica automáticamente a `knowledge/`: `approve_proposal`
solo cambia el estado de la propuesta a "approved" — aplicarla es
responsabilidad del Componente 14 (Versionado del Conocimiento) y de un
humano, nunca de este motor (P7 de la constitución).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from .memory_engine import (
    ConsolidationReport, MemoryItem, MemoryScope, MemoryType, SemanticFact,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    project_id TEXT,
    text TEXT NOT NULL,
    metadata TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON memory_items(type, agent_id, user_id, project_id);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    text TEXT NOT NULL,
    evidence TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);
"""


class SqliteMemoryEngine:
    def __init__(self, db_path: str | Path = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # short_term: puramente en proceso (equivalente a Redis con TTL de sesión),
        # nunca persistido — se pierde al reiniciar, por diseño.
        self._short_term: dict[str, list[tuple[MemoryScope, MemoryItem]]] = {}

    # -- remember / recall --------------------------------------------------
    def remember(self, scope: MemoryScope, item: MemoryItem) -> None:
        if scope.type == MemoryType.SHORT_TERM:
            if not scope.session_id:
                raise ValueError("MemoryType.SHORT_TERM requiere session_id en el scope")
            self._short_term.setdefault(scope.session_id, []).append((scope, item))
            return
        if scope.type == MemoryType.PROJECT and not scope.project_id:
            raise ValueError("MemoryType.PROJECT requiere project_id en el scope")
        self._conn.execute(
            "INSERT INTO memory_items (type, agent_id, user_id, project_id, text, metadata, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope.type.value, scope.agent_id, scope.user_id, scope.project_id,
             item.text, json.dumps(item.metadata, ensure_ascii=False), item.timestamp),
        )
        self._conn.commit()

    def recall(self, scope: MemoryScope, query: str, k: int = 5) -> list[MemoryItem]:
        if scope.type == MemoryType.SHORT_TERM:
            entries = self._short_term.get(scope.session_id or "", [])
            # Filtrar también por agent_id/user_id, no solo por session_id: dos
            # agentes distintos pueden compartir el mismo session_id (una
            # conversación que consulta a más de uno), y sin este filtro cada
            # uno vería los turnos del otro — una fuga de memoria entre
            # agentes, no una feature de "memoria compartida de sesión".
            items = [it for sc, it in entries
                     if sc.agent_id == scope.agent_id and sc.user_id == scope.user_id
                     and (not query or query.lower() in it.text.lower())]
            return items[-k:]

        sql = ("SELECT text, metadata, timestamp FROM memory_items "
               "WHERE type = ? AND agent_id = ? AND user_id = ?")
        params: list = [scope.type.value, scope.agent_id, scope.user_id]
        if scope.type == MemoryType.PROJECT:
            sql += " AND project_id = ?"
            params.append(scope.project_id)
        if query:
            sql += " AND text LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(k)

        rows = self._conn.execute(sql, params).fetchall()
        return [MemoryItem(text=t, metadata=json.loads(m), timestamp=ts) for t, m, ts in rows]

    # -- consolidación (short → long) ----------------------------------------
    def consolidate(self, session_id: str) -> ConsolidationReport:
        entries = self._short_term.pop(session_id, [])
        for scope, item in entries:
            long_scope = MemoryScope(MemoryType.LONG_TERM, scope.agent_id, scope.user_id)
            self.remember(long_scope, item)
        summary = f"{len(entries)} item(s) de la sesión '{session_id}' consolidados a long_term."
        return ConsolidationReport(session_id, len(entries), summary)

    # -- memoria semántica (propuesta, nunca escritura directa — P7) --------
    def propose_semantic(self, agent_id: str, fact: SemanticFact) -> str:
        proposal_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO proposals (id, agent_id, text, evidence, confidence, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'))",
            (proposal_id, agent_id, fact.text, json.dumps(fact.evidence, ensure_ascii=False), fact.confidence),
        )
        self._conn.commit()
        return proposal_id

    def list_proposals(self, *, status: str | None = None) -> list[dict]:
        sql = "SELECT id, agent_id, text, evidence, confidence, status, reviewer, reason, created_at FROM proposals"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["id", "agent_id", "text", "evidence", "confidence", "status", "reviewer", "reason", "created_at"]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            d["evidence"] = json.loads(d["evidence"])
            out.append(d)
        return out

    def approve_proposal(self, proposal_id: str, reviewer: str) -> None:
        self._conn.execute(
            "UPDATE proposals SET status = 'approved', reviewer = ? WHERE id = ?",
            (reviewer, proposal_id))
        self._conn.commit()

    def reject_proposal(self, proposal_id: str, reviewer: str, reason: str) -> None:
        self._conn.execute(
            "UPDATE proposals SET status = 'rejected', reviewer = ?, reason = ? WHERE id = ?",
            (reviewer, reason, proposal_id))
        self._conn.commit()
