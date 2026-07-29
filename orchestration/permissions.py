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

Estado en Runtime: El enforcement de namespaces de conocimiento (`allowed_namespaces`)
está totalmente conectado y activo en `MagnusEngine.ask()`. Las funciones de
comprobación de herramientas (`check_tool()`, `require_tool()`, `rol_puede()`)
están 100% probadas en aislamiento y listas para invocarse tan pronto como el
motor ejecute bucles de herramientas MCP arbitrarias.
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


class NamespaceGranularityError(ValueError):
    """Una política acota `knowledge.read` a una subcarpeta que el RAG no
    puede indexar (ver `kernel/rag/file_store.py:87` — el namespace de un
    chunk es solo el primer segmento de ruta bajo `wiki/`; no existe
    granularidad de subcarpeta). Antes de esta excepción, `allowed_namespaces()`
    devolvía ese patrón acotado como si fuera un namespace válido, y el
    resultado era una consulta que retornaba cero chunks siempre — un
    'mínimo privilegio' que en realidad era un fallo silencioso, no una
    denegación ni un acotamiento real. Corregir la política (declarar el
    namespace de primer nivel completo, o denegarlo del todo) es la única
    salida; no hay forma de honrar la subcarpeta con la arquitectura actual."""


def _coincide(namespace: str, patron: str) -> bool:
    """`*` concede todo; `namespace` debe coincidir exactamente o estar contenido dentro de `patron`."""
    if patron.strip() == "*":
        return True
    p = patron.rstrip("/")
    ns = namespace.rstrip("/")
    return ns == p or ns.startswith(p + "/")


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
        """Los `knowledge.sources` del agente que su política sí autoriza.

        Si la política acota un namespace declarado a una subcarpeta
        (ej. agente declara '01-Finanzas', política solo concede
        '01-Finanzas/personal'), se rechaza con `NamespaceGranularityError`
        en vez de devolver esa subcarpeta como si el RAG pudiera filtrar por
        ella — no puede (ver `NamespaceGranularityError`). Esto es
        intencionalmente una falla ruidosa en config-load/primera consulta,
        no una denegación silenciosa: una política así está mal escrita para
        la arquitectura actual y debe corregirse, no "funcionar a medias".
        """
        policy = self.policy_for(agent)
        permitidos, denegados = [], []
        for ns in agent.knowledge_sources:
            if "*" in policy.knowledge_read or any(_coincide(ns, p) for p in policy.knowledge_read):
                permitidos.append(ns)
                continue
            mas_especificos = [p.rstrip("/") for p in policy.knowledge_read
                                if p.rstrip("/").startswith(ns.rstrip("/") + "/")]
            if mas_especificos:
                raise NamespaceGranularityError(
                    f"agente {agent.id}: la política '{policy.id}' acota '{ns}' a "
                    f"la(s) subcarpeta(s) {mas_especificos}, pero el RAG solo indexa "
                    f"namespaces de primer nivel bajo wiki/ (kernel/rag/file_store.py) "
                    f"y no puede honrar esa granularidad. Declara '{ns}' completo en "
                    f"permissions.knowledge.read, o deniégalo del todo — no una subcarpeta.")
            denegados.append(ns)
        if denegados:
            log.warning(
                "agente %s: la política '%s' deniega la lectura de %s "
                "(declarados en knowledge.sources pero no en permissions.knowledge.read)",
                agent.id, policy.id, denegados)
        return permitidos

    def _agente_declara(self, agent, namespace: str) -> bool:
        ns = namespace.rstrip("/")
        for src in agent.knowledge_sources:
            s = src.rstrip("/")
            if ns == s or ns.startswith(s + "/"):
                return True
        return False

    def can_read(self, agent, namespace: str) -> Decision:
        policy = self.policy_for(agent)
        if not self._agente_declara(agent, namespace):
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
