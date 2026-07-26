"""InheritanceResolver — resuelve `extends` para la jerarquía de agentes.

Ver docs/04-MAGNUS-V2-ARQUITECTURA.md §6:

    CorporateAgent → KnowledgeWorker → {Economist, HealthAdvisor,
    Strategist, TechnicalAdvisor} → agentes concretos

Las bases viven en `agents/_base/<id>/` y NUNCA se cargan como agentes
(AgentRegistry las excluye del descubrimiento por el prefijo `_`). Este
módulo solo sabe fusionar YAML y concatenar Markdown; no valida — la
validación (estructural/referencial) corre sobre el resultado ya fusionado,
en AgentRegistry.

Reglas de fusión (deterministas, auditables):
  - Escalares y dicts: el hijo sobreescribe: campo por campo (merge
    recursivo en dicts), nunca "todo o nada" salvo que el padre no tenga
    ese dict.
  - `tools.allow`, `tools.deny`, `constitution.inherits`: UNIÓN a lo largo
    de la cadena (nunca se pierde algo declarado arriba).
  - `routing.capabilities`: unión por `id`; si dos niveles declaran la
    misma capacidad, el nivel más específico (el hijo) decide `strength`.
  - Tras fusionar tools: `deny` gana sobre `allow` — cualquier id presente
    en `deny` se remueve de `allow`, sin importar en qué nivel se declaró
    `allow`.
  - Markdown: concatenación ancestro→descendiente (nunca sobreescritura
    silenciosa) — cada archivo hereda el contenido del padre y le añade el
    propio debajo.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_LIST_UNION_KEYS = {
    ("tools", "allow"),
    ("tools", "deny"),
    ("constitution", "inherits"),
}


class InheritanceError(ValueError):
    pass


def _merge_capabilities(parent: list[dict], child: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {c["id"]: dict(c) for c in parent}
    for c in child:
        by_id[c["id"]] = dict(c)  # el hijo decide strength si repite el id
    return list(by_id.values())


def _deep_merge(parent: dict, child: dict, path: tuple[str, ...] = ()) -> dict:
    result = dict(parent)
    for key, cval in child.items():
        path_key = path + (key,)
        if path_key == ("routing", "capabilities"):
            result[key] = _merge_capabilities(parent.get(key, []), cval)
        elif path_key in _LIST_UNION_KEYS and isinstance(cval, list):
            merged = list(parent.get(key, []))
            for item in cval:
                if item not in merged:
                    merged.append(item)
            result[key] = merged
        elif isinstance(cval, dict) and isinstance(parent.get(key), dict):
            result[key] = _deep_merge(parent[key], cval, path_key)
        else:
            result[key] = cval
    return result


def _apply_deny_wins(merged: dict) -> dict:
    tools = merged.get("tools")
    if not isinstance(tools, dict):
        return merged
    deny = set(tools.get("deny", []))
    allow = [a for a in tools.get("allow", []) if a not in deny]
    tools["allow"] = allow
    return merged


class InheritanceResolver:
    """Cachea el `agent.yaml` ya parseado de cada carpeta (leaf + toda la
    cadena de `_base/`) — evita releer y re-parsear el mismo archivo varias
    veces por agente durante `load_all()` (antes: hasta 4 lecturas
    redundantes por agente, ~1.5s en 5 agentes solo en I/O+parseo de YAML).
    El caché vive mientras vive el `AgentRegistry`; se invalida solo en un
    `reload()`/`load_all()` nuevo (se crea un `InheritanceResolver` con cada
    `AgentRegistry`, así que nunca sirve datos obsoletos entre cargas)."""

    def __init__(self, base_root: str | Path):
        self.base_root = Path(base_root)
        self._yaml_cache: dict[Path, dict] = {}

    def read_agent_yaml(self, dir_path: Path) -> dict:
        if dir_path not in self._yaml_cache:
            self._yaml_cache[dir_path] = yaml.safe_load(
                (dir_path / "agent.yaml").read_text(encoding="utf-8")) or {}
        return self._yaml_cache[dir_path]

    def _base_dir(self, base_id: str) -> Path:
        d = self.base_root / base_id
        if not d.exists():
            raise InheritanceError(f"base de herencia inexistente: '{base_id}' (agents/_base/{base_id})")
        return d

    def _chain(self, agent_dir: Path) -> list[Path]:
        """Devuelve [raíz, ..., agent_dir] siguiendo `extends` hacia arriba."""
        chain: list[Path] = [agent_dir]
        seen: set[str] = {str(agent_dir)}
        cur = agent_dir
        while True:
            raw = self.read_agent_yaml(cur)
            parent_id = raw.get("extends")
            if not parent_id:
                break
            parent_dir = self._base_dir(parent_id)
            if str(parent_dir) in seen:
                raise InheritanceError(f"ciclo de herencia detectado en la cadena de {agent_dir.name}")
            seen.add(str(parent_dir))
            chain.append(parent_dir)
            cur = parent_dir
        return list(reversed(chain))  # raíz primero

    def chain_of(self, agent_dir: str | Path) -> list[str]:
        return [d.name for d in self._chain(Path(agent_dir))]

    def resolve_yaml(self, agent_dir: Path) -> dict:
        chain = self._chain(agent_dir)
        merged: dict = {}
        for d in chain:
            raw = dict(self.read_agent_yaml(d))
            raw.pop("extends", None)
            merged = _deep_merge(merged, raw)
        return _apply_deny_wins(merged)

    def resolve_markdown(self, agent_dir: Path, filename: str) -> str:
        chain = self._chain(agent_dir)
        parts: list[str] = []
        for d in chain:
            p = d / filename
            if p.exists():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
        return "\n\n---\n\n".join(parts)
