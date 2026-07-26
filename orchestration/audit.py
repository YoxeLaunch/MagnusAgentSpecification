"""Registro trazable de consultas (paso 3.5 del ROADMAP).

Guarda, por consulta y agente: la decisión de evaluación (score, pass/fail,
razón), los identificadores y **hashes** de los chunks recuperados, y el
snapshot de la wiki con el que se respondió. Con eso una cita publicada se
puede auditar después: si el hash del pasaje ya no coincide, la nota cambió
desde que se emitió la cita, y se sabe.

Privacidad — decisión deliberada
--------------------------------
La LLM-Wiki contiene información personal, financiera y de salud. Por eso:

  - El registro está **desactivado por defecto**. Se activa explícitamente
    (`MAGNUS_TRACE_DIR=...` en el servidor MCP, o pasando un `TraceStore` al
    motor). Nada se escribe a disco si nadie lo pidió.
  - Se guardan referencias (ruta, chunk_id, hash), **nunca el texto de los
    pasajes** ni la respuesta generada. Reproducir una cita no exige duplicar
    el contenido de la nota.
  - El directorio por defecto (`traces/`) está en `.gitignore`.

La consulta del usuario SÍ se guarda: sin ella el registro no permite auditar
por qué se recuperó lo que se recuperó. Es el dato más sensible del archivo y
por eso el registro es opt-in. La política formal de egreso y retención es el
paso 5 del ROADMAP; esto deja el gancho puesto sin adelantarla.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class TraceStore(Protocol):
    def record(self, entry: dict) -> None: ...


@dataclass
class NullTraceStore:
    """No escribe nada. Es el comportamiento por defecto del motor."""

    def record(self, entry: dict) -> None:  # noqa: D102
        return None


class JsonlTraceStore:
    """Un archivo JSONL por día, en append. Sin dependencias externas."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        dia = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.directory / f"magnus-{dia}.jsonl"

    def record(self, entry: dict) -> None:
        linea = json.dumps(entry, ensure_ascii=False)
        with open(self._path(), "a", encoding="utf-8") as f:
            f.write(linea + "\n")


def trace_store_from_env(var: str = "MAGNUS_TRACE_DIR") -> TraceStore:
    """`JsonlTraceStore` si la variable está puesta; si no, no registra nada."""
    directorio = os.environ.get(var, "").strip()
    return JsonlTraceStore(directorio) if directorio else NullTraceStore()


def build_entry(*, agent_id: str, question: str, chunks, evaluation: dict | None,
                guardrails: dict | None, provider_trace: dict, mode: str) -> dict:
    """Arma la entrada de auditoría de un agente para una consulta."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agente": agent_id,
        "consulta": question,
        "modo": mode,
        "conocimiento": {
            # el snapshot es el mismo para todos los chunks de una ingesta
            "version": next((c.provenance.knowledge_version for c in chunks), None),
            "chunks": [
                {"id": c.chunk_id, "hash": c.provenance.hash,
                 "fuente": c.provenance.source, "score": c.score}
                for c in chunks
            ],
        },
        "evaluacion": evaluation,
        "guardrails": guardrails,
        "proveedor": provider_trace,
    }
