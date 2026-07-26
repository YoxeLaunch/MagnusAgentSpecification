"""Catálogo de Capability — independiente de los agentes que las implementan.

Carga `capabilities/*.capability.yaml` (ver docs/04-MAGNUS-V2-ARQUITECTURA.md
§3). Es la fuente que alimenta el índice de matching del Capability Engine;
el Registro de Agentes solo la referencia para validar `routing.capabilities[].id`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    description: str
    parent: str | None = None
    related: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    routing_examples: list[str] = field(default_factory=list)


class CapabilityCatalog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._by_id: dict[str, Capability] = {}

    def load_all(self) -> "CapabilityCatalog":
        self._by_id.clear()
        if not self.root.exists():
            return self
        for path in sorted(self.root.glob("*.capability.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            cap = Capability(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                description=raw.get("description", ""),
                parent=raw.get("parent"),
                related=list(raw.get("related", [])),
                synonyms=list(raw.get("synonyms", [])),
                routing_examples=list(raw.get("routing_examples", [])),
            )
            self._by_id[cap.id] = cap
        self._check_no_cycles()
        self._check_references()
        return self

    def _check_no_cycles(self) -> None:
        for cap_id in self._by_id:
            seen = {cap_id}
            cur = self._by_id[cap_id].parent
            while cur is not None:
                if cur in seen:
                    raise ValueError(f"ciclo de herencia detectado en capacidad '{cap_id}'")
                seen.add(cur)
                cur = self._by_id.get(cur).parent if cur in self._by_id else None

    def _check_references(self) -> None:
        for cap in self._by_id.values():
            if cap.parent is not None and cap.parent not in self._by_id:
                raise ValueError(f"capacidad '{cap.id}': parent desconocido '{cap.parent}'")
            for rel_id in cap.related:
                if rel_id not in self._by_id:
                    raise ValueError(f"capacidad '{cap.id}': related desconocido '{rel_id}'")

    def ancestors_of(self, capability_id: str) -> list[str]:
        """[padre, abuelo, ...] en orden ascendente, o [] si no hay o no existe."""
        out: list[str] = []
        cur = self._by_id.get(capability_id)
        cur_parent = cur.parent if cur else None
        while cur_parent is not None:
            out.append(cur_parent)
            nxt = self._by_id.get(cur_parent)
            cur_parent = nxt.parent if nxt else None
        return out

    def get(self, capability_id: str) -> Capability | None:
        return self._by_id.get(capability_id)

    def exists(self, capability_id: str) -> bool:
        return capability_id in self._by_id

    def all(self) -> list[Capability]:
        return list(self._by_id.values())

    def ids(self) -> set[str]:
        return set(self._by_id.keys())
