"""Enforcement de permisos (paso 5.1 del ROADMAP).

Hasta ahora `configs/permissions.yaml` solo se validaba: `AgentRegistry`
comprobaba que `permissions.policy_ref` existiera y ahí terminaba todo. La
política se escribía, se referenciaba… y nunca se aplicaba. Este módulo la
aplica.

Permiso efectivo = intersección de tres fuentes, con denegación por defecto
------------------------------------------------------------------------------
1. Lo que el agente declara en su `agent.yaml` (`tools.allow` / `tools.deny`,
   `knowledge.sources`).
2. Lo que su política permite en `configs/permissions.yaml`.
3. Lo que el usuario llamante tiene autorizado por su rol.

Cualquier desacuerdo entre las tres deniega. Y `deny` gana siempre sobre
`allow`, en cualquiera de los niveles: si algo aparece denegado en un sitio, no
hay `allow` en otro que lo rescate.

Consecuencia práctica de aplicar la política de verdad: se descubrió que
`economist_readonly` concedía lectura sobre `economics/`, `finance/`, `imf/`…
—los namespaces de la wiki ANTES de reorganizarla— mientras `ernesto_libras`
declara `01-Economia-y-Finanzas`. Con enforcement real, ese agente se quedaba
sin poder leer nada. Es exactamente el tipo de deriva que una política escrita
pero nunca aplicada acumula sin que nadie se entere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("magnus.permissions")

#: Rol por defecto de quien llama sin identificarse (p. ej. el servidor MCP
#: local). Es el rol MENOS privilegiado que puede consultar.
ROL_POR_DEFECTO = "operator"


@dataclass(frozen=True)
class Policy:
    id: str
    knowledge_read: list[str] = field(default_factory=list)
    knowledge_write: list[str] = field(default_factory=list)
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    external_side_effects: str = "require_human_approval"

    def permite_leer(self, namespace: str) -> bool:
        return any(_coincide(namespace, patron) for patron in self.knowledge_read)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


class PermissionDenied(PermissionError):
    """La política efectiva no autoriza la operación."""


def _coincide(namespace: str, patron: str) -> bool:
    """`*` concede todo; el resto compara por prefijo de ruta."""
    if patron.strip() == "*":
        return True
    p = patron.rstrip("/")
    ns = namespace.rstrip("/")
    return ns == p or ns.startswith(p + "/") or p.startswith(ns + "/")


class PermissionEngine:
    def __init__(self, policies: dict[str, Policy], roles: dict[str, list[str]]):
        self._policies = policies
        self._roles = roles

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PermissionEngine":
        p = Path(path)
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}
        policies = {}
        for pid, cfg in (raw.get("policies") or {}).items():
            knowledge = cfg.get("knowledge") or {}
            tools = cfg.get("tools") or {}
            actions = cfg.get("actions") or {}
            policies[pid] = Policy(
                id=pid,
                knowledge_read=list(knowledge.get("read", [])),
                knowledge_write=list(knowledge.get("write", [])),
                tools_allow=list(tools.get("allow", [])),
                tools_deny=list(tools.get("deny", [])),
                external_side_effects=actions.get("external_side_effects",
                                                  "require_human_approval"),
            )
        roles = {rid: list((cfg or {}).get("can", []))
                 for rid, cfg in (raw.get("roles") or {}).items()}
        return cls(policies, roles)

    # -- consulta -------------------------------------------------------------
    def policy_for(self, agent) -> Policy:
        pid = agent.permissions_policy_ref
        if pid not in self._policies:
            # Denegación por defecto: una política que no existe no concede nada.
            log.error("agente %s referencia la política inexistente '%s'", agent.id, pid)
            return Policy(id=pid)
        return self._policies[pid]

    def rol_puede(self, rol: str, accion: str) -> bool:
        permisos = self._roles.get(rol)
        if permisos is None:
            return False
        return "*" in permisos or accion in permisos

    # -- conocimiento ---------------------------------------------------------
    def allowed_namespaces(self, agent) -> list[str]:
        """Los `knowledge.sources` del agente que su política sí autoriza."""
        policy = self.policy_for(agent)
        permitidos, denegados = [], []
        for ns in agent.knowledge_sources:
            (permitidos if policy.permite_leer(ns) else denegados).append(ns)
        if denegados:
            log.warning(
                "agente %s: la política '%s' deniega la lectura de %s "
                "(declarados en knowledge.sources pero no en permissions.knowledge.read)",
                agent.id, policy.id, denegados)
        return permitidos

    def can_read(self, agent, namespace: str) -> Decision:
        policy = self.policy_for(agent)
        if namespace not in agent.knowledge_sources:
            return Decision(False, f"'{namespace}' no está en knowledge.sources de {agent.id}")
        if not policy.permite_leer(namespace):
            return Decision(False, f"la política '{policy.id}' no concede lectura de '{namespace}'")
        return Decision(True, f"'{namespace}' autorizado por la política '{policy.id}'")

    # -- herramientas ---------------------------------------------------------
    def effective_tools(self, agent) -> set[str]:
        """Intersección de lo declarado por el agente y lo que concede su política."""
        policy = self.policy_for(agent)
        permitidas = set(agent.tools_allow) & set(policy.tools_allow)
        return permitidas - set(agent.tools_deny) - set(policy.tools_deny)

    def check_tool(self, agent, tool: str, *, rol: str = ROL_POR_DEFECTO) -> Decision:
        policy = self.policy_for(agent)
        if tool in set(agent.tools_deny) | set(policy.tools_deny):
            return Decision(False, f"'{tool}' está explícitamente denegada (deny gana sobre allow)")
        if tool not in agent.tools_allow:
            return Decision(False, f"{agent.id} no declara '{tool}' en tools.allow")
        if tool not in policy.tools_allow:
            return Decision(False, f"la política '{policy.id}' no concede '{tool}'")
        if not self.rol_puede(rol, "*") and not self.rol_puede(rol, "tools.use"):
            # Los roles de permissions.yaml describen acciones de alto nivel
            # (query, agents.read…). Usar herramientas exige 'tools.use' o '*'.
            return Decision(False, f"el rol '{rol}' no está autorizado a usar herramientas")
        return Decision(True, f"'{tool}' autorizada por {agent.id} + política '{policy.id}' + rol '{rol}'")

    def require_tool(self, agent, tool: str, *, rol: str = ROL_POR_DEFECTO) -> None:
        decision = self.check_tool(agent, tool, rol=rol)
        if not decision:
            raise PermissionDenied(f"{agent.id} → '{tool}': {decision.reason}")

    # -- introspección --------------------------------------------------------
    def policies(self) -> list[str]:
        return sorted(self._policies)

    def roles(self) -> list[str]:
        return sorted(self._roles)
