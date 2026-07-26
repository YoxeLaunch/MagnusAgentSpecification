"""Agent Registry — única fuente de verdad de qué agentes existen.

Ver docs/04-MAGNUS-V2-ARQUITECTURA.md §2. Carga `agents/*/agent.yaml`,
valida (estructural + referencial) y expone AgentSpec al resto del runtime.
Ningún otro componente debe leer `agents/` directamente ni definir agentes
en código — esta es la regla que cierra la brecha detectada en la auditoría.

Validación en dos niveles (§5):
  1. Estructural — campos requeridos, tipos, enums. Puro, sin I/O externo.
  2. Referencial — capacidades existen en el catálogo, model.profile existe
     en models.yaml, permissions.policy_ref existe en permissions.yaml,
     y (si status=active) los 12 Markdown existen con contenido.

No depende de `jsonschema` (no está entre las dependencias del proyecto);
la validación estructural se implementa en Python puro, reflejando
`schemas/agent.schema.json` campo a campo. Si diverge de ese archivo, el
schema JSON manda como documentación normativa y este código debe corregirse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .capability_catalog import CapabilityCatalog
from .inheritance import InheritanceResolver, InheritanceError

REQUIRED_MD_FILES = [
    "identity.md", "mission.md", "personality.md", "principles.md",
    "skills.md", "knowledge.md", "tools.md", "permissions.md",
    "memory.md", "examples.md", "evaluation.md", "workflows.md",
]

_RESERVED_DIR_PREFIXES = ("_",)  # _template, _base/* nunca son agentes


@dataclass(frozen=True)
class CapabilityRef:
    id: str
    strength: str  # "primary" | "secondary"


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    role: str
    version: str
    status: str
    capabilities: list[CapabilityRef]
    priority: int
    knowledge_sources: list[str]
    top_k: int
    min_score: float
    model_profile: str
    fallback_profile: str | None
    effort: str | None
    tools_allow: list[str]
    tools_deny: list[str]
    mcp_servers: list[dict]
    permissions_policy_ref: str
    rubric_ref: str
    rigor: int
    require_citations: bool
    path: Path
    inheritance_chain: list[str] = field(default_factory=list)
    raw: dict = field(repr=False, default_factory=dict)
    _resolver: "InheritanceResolver | None" = field(repr=False, default=None, compare=False)

    def markdown(self, filename: str) -> str:
        """Contenido efectivo del archivo: fusión ancestro→descendiente si el
        agente hereda (`extends`), o solo el propio si no hereda de nada."""
        if self._resolver is not None:
            return self._resolver.resolve_markdown(self.path, filename)
        p = self.path / filename
        return p.read_text(encoding="utf-8") if p.exists() else ""


@dataclass
class ValidationResult:
    agent_id: str
    ok: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class LoadReport:
    loaded: list[str]
    invalid: list[ValidationResult]


class AgentRegistry:
    def __init__(self, agents_root: str | Path, *, capabilities: CapabilityCatalog,
                 models_yaml: str | Path, permissions_yaml: str | Path,
                 mcp_catalog_yaml: str | Path | None = None):
        self.root = Path(agents_root)
        self._capabilities = capabilities
        self._model_profiles = self._load_profile_names(models_yaml)
        self._permission_refs = self._load_policy_refs(permissions_yaml)
        self._mcp_servers = self._load_mcp_catalog(mcp_catalog_yaml) if mcp_catalog_yaml else {}
        self._resolver = InheritanceResolver(self.root / "_base")
        self._specs: dict[str, AgentSpec] = {}
        self._invalid: dict[str, ValidationResult] = {}
        self.index_version: int = 0

    @staticmethod
    def _load_profile_names(path: str | Path) -> set[str]:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return set(raw.get("profiles", {}).keys())

    @staticmethod
    def _load_policy_refs(path: str | Path) -> set[str]:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return set(raw.get("policies", {}).keys())

    @staticmethod
    def _load_mcp_catalog(path: str | Path) -> dict[str, list[str]]:
        p = Path(path)
        if not p.exists():
            return {}
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {sid: cfg.get("scopes", []) for sid, cfg in raw.get("servers", {}).items()}

    # -- descubrimiento -----------------------------------------------------
    def _candidate_dirs(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            d for d in self.root.iterdir()
            if d.is_dir() and not d.name.startswith(_RESERVED_DIR_PREFIXES)
            and (d / "agent.yaml").exists()
        )

    def load_all(self) -> LoadReport:
        self._specs.clear()
        self._invalid.clear()
        self._resolver._yaml_cache.clear()
        loaded: list[str] = []
        for d in self._candidate_dirs():
            result, spec = self._load_one(d)
            if result.ok and spec is not None:
                self._specs[spec.id] = spec
                loaded.append(spec.id)
            else:
                self._invalid[d.name] = result
        self.index_version += 1
        return LoadReport(loaded=loaded, invalid=list(self._invalid.values()))

    def reload(self, agent_id: str) -> AgentSpec:
        """Hot-reload de un solo agente, sin recargar los otros N-1 (§7:
        necesario a escala de cientos de agentes)."""
        d = self.root / agent_id
        if not d.exists() or not (d / "agent.yaml").exists():
            raise KeyError(f"agente desconocido: {agent_id}")
        self._resolver._yaml_cache.pop(d, None)
        result, spec = self._load_one(d)
        if not result.ok or spec is None:
            self._invalid[agent_id] = result
            self._specs.pop(agent_id, None)
            raise ValueError(f"'{agent_id}' no valida tras reload: {result.errors}")
        self._specs[spec.id] = spec
        self._invalid.pop(agent_id, None)
        self.index_version += 1
        return spec

    def _load_one(self, path: Path) -> tuple[ValidationResult, AgentSpec | None]:
        agent_id = path.name
        try:
            leaf_raw = self._resolver.read_agent_yaml(path)
            if leaf_raw.get("extends"):
                raw = self._resolver.resolve_yaml(path)
            else:
                raw = leaf_raw
        except InheritanceError as e:
            return ValidationResult(agent_id, False, [f"herencia inválida: {e}"]), None
        except Exception as e:
            return ValidationResult(agent_id, False, [f"agent.yaml inválido: {e}"]), None

        errors = self._validate_structural(raw)
        if errors:
            return ValidationResult(agent_id, False, errors), None

        errors += self._validate_referential(raw, path)
        if errors:
            return ValidationResult(agent_id, False, errors), None

        spec = self._to_spec(raw, path)
        return ValidationResult(agent_id, True, []), spec

    # -- validación estructural (refleja schemas/agent.schema.json) ---------
    def _validate_structural(self, raw: dict) -> list[str]:
        errors: list[str] = []
        required_top = ["mas_version", "id", "name", "role", "version", "status",
                         "routing", "knowledge", "model", "tools", "permissions",
                         "evaluation", "constitution"]
        for key in required_top:
            if key not in raw:
                errors.append(f"falta campo obligatorio: {key}")
        if errors:
            return errors  # sin los campos base no tiene sentido seguir

        if raw["status"] not in ("draft", "active", "deprecated", "invalid"):
            errors.append(f"status inválido: {raw['status']}")

        routing = raw.get("routing", {})
        caps = routing.get("capabilities", [])
        if not caps:
            errors.append("routing.capabilities no puede estar vacío")
        for c in caps:
            if "id" not in c or "strength" not in c:
                errors.append(f"routing.capabilities entrada inválida: {c}")
            elif c["strength"] not in ("primary", "secondary"):
                errors.append(f"strength inválido para capacidad {c.get('id')}: {c['strength']}")

        model = raw.get("model", {})
        if "profile" not in model:
            errors.append("model.profile es obligatorio")

        tools = raw.get("tools", {})
        if "allow" not in tools or "deny" not in tools:
            errors.append("tools.allow y tools.deny son obligatorios")

        permissions = raw.get("permissions", {})
        if "policy_ref" not in permissions:
            errors.append("permissions.policy_ref es obligatorio")

        evaluation = raw.get("evaluation", {})
        for key in ("rubric_ref", "rigor", "require_citations"):
            if key not in evaluation:
                errors.append(f"evaluation.{key} es obligatorio")

        constitution = raw.get("constitution", {})
        if "magnus_constitution" not in constitution.get("inherits", []):
            errors.append("constitution.inherits debe incluir 'magnus_constitution'")

        return errors

    # -- validación referencial (requiere el resto del sistema cargado) -----
    def _validate_referential(self, raw: dict, path: Path) -> list[str]:
        errors: list[str] = []

        for c in raw["routing"]["capabilities"]:
            if not self._capabilities.exists(c["id"]):
                errors.append(f"capacidad desconocida (no está en capabilities/*.yaml): {c['id']}")

        profile = raw["model"]["profile"]
        if profile not in self._model_profiles:
            errors.append(f"model.profile no existe en configs/models.yaml: {profile}")

        fallback = raw["model"].get("fallback_profile")
        if fallback and fallback not in self._model_profiles:
            errors.append(f"model.fallback_profile no existe en configs/models.yaml: {fallback}")

        policy_ref = raw["permissions"]["policy_ref"]
        if policy_ref not in self._permission_refs:
            errors.append(f"permissions.policy_ref no existe en configs/permissions.yaml: {policy_ref}")

        if self._mcp_servers:
            for entry in raw.get("tools", {}).get("mcp_servers", []):
                sid, scope = entry.get("id"), entry.get("scope")
                if sid not in self._mcp_servers:
                    errors.append(f"tools.mcp_servers referencia un servidor desconocido: {sid} "
                                  f"(no está en tools/mcp_catalog.yaml)")
                elif scope not in self._mcp_servers[sid]:
                    errors.append(f"tools.mcp_servers[{sid}].scope='{scope}' no soportado "
                                  f"(admite: {self._mcp_servers[sid]})")

        if raw["status"] == "active":
            has_extends = bool(self._resolver.read_agent_yaml(path).get("extends"))
            for filename in REQUIRED_MD_FILES:
                content = (self._resolver.resolve_markdown(path, filename) if has_extends
                           else (path / filename).read_text(encoding="utf-8") if (path / filename).exists() else "")
                if not content.strip():
                    errors.append(f"status=active exige contenido en {filename}, falta o está vacío "
                                  f"(considerando herencia: {has_extends})")
                elif "<!-- completa" in content:
                    errors.append(f"{filename} todavía tiene placeholders sin completar "
                                  f"('<!-- completa ... -->') — no puede activarse así (Fase 5, SDK)")

        return errors

    def _to_spec(self, raw: dict, path: Path) -> AgentSpec:
        routing = raw["routing"]
        knowledge = raw["knowledge"]
        model = raw["model"]
        tools = raw["tools"]
        evaluation = raw["evaluation"]
        leaf_raw = self._resolver.read_agent_yaml(path)
        chain = self._resolver.chain_of(path) if leaf_raw.get("extends") else [path.name]
        resolver_ref = self._resolver if leaf_raw.get("extends") else None
        return AgentSpec(
            id=raw["id"],
            name=raw["name"],
            role=raw["role"],
            version=raw["version"],
            status=raw["status"],
            capabilities=[CapabilityRef(c["id"], c["strength"]) for c in routing["capabilities"]],
            priority=routing.get("priority", 5),
            knowledge_sources=list(knowledge.get("sources", [])),
            top_k=knowledge.get("retrieval", {}).get("top_k", 8),
            min_score=knowledge.get("retrieval", {}).get("min_score", 0.35),
            model_profile=model["profile"],
            fallback_profile=model.get("fallback_profile"),
            effort=model.get("effort"),
            tools_allow=list(tools.get("allow", [])),
            tools_deny=list(tools.get("deny", [])),
            mcp_servers=list(tools.get("mcp_servers", [])),
            permissions_policy_ref=raw["permissions"]["policy_ref"],
            rubric_ref=evaluation["rubric_ref"],
            rigor=evaluation["rigor"],
            require_citations=evaluation["require_citations"],
            path=path,
            inheritance_chain=chain,
            raw=raw,
            _resolver=resolver_ref,
        )

    def validate_as(self, agent_dir_name: str, status_override: str) -> ValidationResult:
        """Valida como si `status` fuera `status_override`, sin tocar el
        archivo en disco. Usado por el SDK (`magnus agent activate`) para
        comprobar que un agente en draft cumpliría los requisitos de
        `active` antes de escribir el cambio."""
        path = self.root / agent_dir_name
        if not (path / "agent.yaml").exists():
            return ValidationResult(agent_dir_name, False, ["agent.yaml no existe"])
        try:
            leaf_raw = self._resolver.read_agent_yaml(path)
            raw = self._resolver.resolve_yaml(path) if leaf_raw.get("extends") else leaf_raw
        except (InheritanceError, Exception) as e:  # noqa: BLE001
            return ValidationResult(agent_dir_name, False, [f"error de carga: {e}"])
        raw = dict(raw)
        raw["status"] = status_override
        errors = self._validate_structural(raw)
        if errors:
            return ValidationResult(agent_dir_name, False, errors)
        errors += self._validate_referential(raw, path)
        return ValidationResult(agent_dir_name, len(errors) == 0, errors)

    # -- API pública ----------------------------------------------------------
    def get(self, agent_id: str) -> AgentSpec:
        if agent_id not in self._specs:
            raise KeyError(f"agente desconocido o inválido: {agent_id}")
        return self._specs[agent_id]

    def list(self, *, status: str | None = None) -> list[AgentSpec]:
        specs = list(self._specs.values())
        if status:
            specs = [s for s in specs if s.status == status]
        return sorted(specs, key=lambda s: s.id)

    def invalid(self) -> list[ValidationResult]:
        return list(self._invalid.values())

    def capabilities_of(self, agent_id: str) -> list[CapabilityRef]:
        return self.get(agent_id).capabilities

    def agents_for_capability(self, capability_id: str, *, status: str = "active") -> list[AgentSpec]:
        out = []
        for spec in self.list(status=status):
            if any(c.id == capability_id for c in spec.capabilities):
                out.append(spec)
        return out
