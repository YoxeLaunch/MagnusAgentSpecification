#!/usr/bin/env python
"""Magnus Agent SDK — CLI.

Convierte la especificación MAS en producto: crear un agente pasa a ser un
comando, generado contra `agent.schema.json` y el catálogo de capacidades
vigente, en vez de "copiar una carpeta y editar con cuidado"
(docs/04-MAGNUS-V2-ARQUITECTURA.md §13).

Uso (desde la raíz del proyecto):
    python sdk/cli.py agent create serena --name Serena --role "..." \\
        --capability mental_health:primary --extends health_advisor \\
        --source 03-Salud-Mental-y-Desarrollo-Personal --policy wellbeing_readonly
    python sdk/cli.py agent validate serena
    python sdk/cli.py agent activate serena
    python sdk/cli.py agent deprecate serena
    python sdk/cli.py agent explain serena "estoy estresado"
    python sdk/cli.py agent test serena
    python sdk/cli.py capability create brand_strategy --name "Brand Strategy" ...
    python sdk/cli.py capability list
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestration.registry.agent_registry import AgentRegistry  # noqa: E402
from orchestration.registry.capability_catalog import CapabilityCatalog  # noqa: E402
from orchestration.capability_engine import CapabilityEngine  # noqa: E402
from sdk import scaffold  # noqa: E402


def _build_registry() -> tuple[CapabilityCatalog, AgentRegistry]:
    capabilities = CapabilityCatalog(ROOT / "capabilities").load_all()
    registry = AgentRegistry(
        ROOT / "agents",
        capabilities=capabilities,
        models_yaml=ROOT / "configs" / "models.yaml",
        permissions_yaml=ROOT / "configs" / "permissions.yaml",
        mcp_catalog_yaml=ROOT / "tools" / "mcp_catalog.yaml",
    )
    registry.load_all()
    return capabilities, registry


def _parse_capability(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError(f"formato esperado id:strength, recibido '{spec}'")
    cap_id, strength = spec.split(":", 1)
    if strength not in ("primary", "secondary"):
        raise argparse.ArgumentTypeError(f"strength debe ser primary|secondary, recibido '{strength}'")
    return cap_id, strength


# -- agent create -------------------------------------------------------------
def cmd_agent_create(args: argparse.Namespace) -> int:
    agent_dir = ROOT / "agents" / args.id
    if agent_dir.exists() and not args.force:
        print(f"✗ agents/{args.id}/ ya existe. Usa --force para sobrescribir.", file=sys.stderr)
        return 1

    capabilities, _ = _build_registry()
    unknown = [cid for cid, _ in args.capability if not capabilities.exists(cid)]
    if unknown:
        print(f"✗ capacidad(es) desconocida(s), créalas primero con "
              f"'magnus capability create': {unknown}", file=sys.stderr)
        return 1

    agent_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or args.id.replace("_", " ").title()
    role = args.role or f"Agente de Magnus Dynamic Group ({args.id})"

    yaml_text = scaffold.agent_yaml(
        agent_id=args.id, name=name, role=role, capabilities=args.capability,
        extends=args.extends, profile=args.profile, priority=args.priority,
        sources=args.source, policy_ref=args.policy,
    )
    (agent_dir / "agent.yaml").write_text(yaml_text, encoding="utf-8")

    for filename, content in scaffold.markdown_files(name, role).items():
        (agent_dir / filename).write_text(content, encoding="utf-8")

    print(f"✓ agents/{args.id}/ creado en status: draft (13 archivos).")
    print(f"  Completa los placeholders '<!-- completa -->' y luego:")
    print(f"    python sdk/cli.py agent validate {args.id}")
    print(f"    python sdk/cli.py agent activate {args.id}")
    return 0


# -- agent validate / activate / deprecate ------------------------------------
def cmd_agent_validate(args: argparse.Namespace) -> int:
    _, registry = _build_registry()
    ids_loaded = {a.id: a for a in registry.list()}
    if args.id in ids_loaded:
        status = ids_loaded[args.id].status
        print(f"✓ '{args.id}' válido para su status actual ({status}).")
        if status == "draft":
            print("  (nota: 'draft' no exige los 12 .md completos ni sin placeholders; "
                  "usa 'agent activate' para comprobar los requisitos de 'active')")
        return 0
    invalid = {v.agent_id: v for v in registry.invalid()}
    if args.id in invalid:
        print(f"✗ '{args.id}' inválido:")
        for e in invalid[args.id].errors:
            print(f"  - {e}")
        return 1
    print(f"✗ '{args.id}' no existe en agents/.", file=sys.stderr)
    return 1


def cmd_agent_activate(args: argparse.Namespace) -> int:
    _, registry = _build_registry()
    result = registry.validate_as(args.id, "active")
    if not result.ok:
        print(f"✗ '{args.id}' no cumple los requisitos de status: active:")
        for e in result.errors:
            print(f"  - {e}")
        return 1
    _set_status(args.id, "active")
    print(f"✓ '{args.id}' activado (status: active).")
    return 0


def cmd_agent_deprecate(args: argparse.Namespace) -> int:
    _set_status(args.id, "deprecated")
    print(f"✓ '{args.id}' marcado como deprecated.")
    return 0


def _set_status(agent_id: str, new_status: str) -> None:
    path = ROOT / "agents" / agent_id / "agent.yaml"
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(r"^status:\s*\S+", f"status: {new_status}", text, count=1, flags=re.M)
    if n == 0:
        raise ValueError(f"no se encontró 'status:' en {path}")
    path.write_text(new_text, encoding="utf-8")


# -- agent explain / test ------------------------------------------------------
def cmd_agent_explain(args: argparse.Namespace) -> int:
    capabilities, registry = _build_registry()
    engine = CapabilityEngine(capabilities, registry)
    explanation = engine.explain(args.query, args.id)
    print(f"query:      {explanation.query}")
    print(f"agente:     {explanation.agent_id}")
    print(f"umbral:     {explanation.threshold}")
    print(f"score:      {explanation.agent_score}")
    print(f"capacidades con match: {[(m.capability_id, m.via) for m in explanation.capability_scores]}")
    print(f"razón:      {explanation.reason}")
    if explanation.channel_scores:
        print("desglose por canal (léxico / vectorial / final):")
        for c in explanation.channel_scores:
            print(f"  - {c.capability_id:<24} lex={c.lexical_score:.3f}  "
                  f"vec={c.embedding_score:.3f}  final={c.final_score:.3f}  "
                  f"vía={c.via:<9} · {c.reason}")
    return 0


def cmd_agent_test(args: argparse.Namespace) -> int:
    """Corre examples.md como casos de contrato — versión mínima: cuenta
    ejemplos 'Correcto'/'Incorrecto' declarados. NO ejecuta todavía el
    Evaluador real (Componente 9, fuera del alcance de esta fase); es el
    punto de extensión para cuando ese componente exista."""
    _, registry = _build_registry()
    spec = registry.get(args.id)
    content = spec.markdown("examples.md")
    correctos = len(re.findall(r"^##\s*Correcto", content, re.M))
    incorrectos = len(re.findall(r"^##\s*Incorrecto", content, re.M))
    if correctos == 0:
        print(f"✗ '{args.id}': examples.md no declara ningún caso 'Correcto'.", file=sys.stderr)
        return 1
    print(f"✓ '{args.id}': {correctos} caso(s) correcto(s), {incorrectos} caso(s) incorrecto(s) "
          f"declarados en examples.md.")
    print("  (contrato leído; ejecución automática contra el Evaluador pendiente del Componente 9)")
    return 0


# -- capability create / list --------------------------------------------------
def cmd_capability_create(args: argparse.Namespace) -> int:
    path = ROOT / "capabilities" / f"{args.id}.capability.yaml"
    if path.exists() and not args.force:
        print(f"✗ {path} ya existe. Usa --force.", file=sys.stderr)
        return 1
    text = scaffold.capability_yaml(
        cap_id=args.id, name=args.name or args.id.replace("_", " ").title(),
        description=args.description or "<!-- completa: descripción -->",
        parent=args.parent, examples=args.example,
    )
    path.write_text(text, encoding="utf-8")
    print(f"✓ {path} creado.")
    return 0


def cmd_capability_list(args: argparse.Namespace) -> int:
    capabilities, registry = _build_registry()
    for cap in sorted(capabilities.all(), key=lambda c: c.id):
        agents = registry.agents_for_capability(cap.id)
        tag = ", ".join(a.id for a in agents) if agents else "— HUÉRFANA (sin agente) —"
        print(f"{cap.id:<24} {tag}")
    return 0


# -- argparse wiring ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="magnus", description="Magnus Agent SDK")
    sub = p.add_subparsers(dest="entity", required=True)

    agent = sub.add_parser("agent", help="gestión de agentes MAS")
    agent_sub = agent.add_subparsers(dest="action", required=True)

    create = agent_sub.add_parser("create", help="genera el esqueleto MAS de un agente nuevo")
    create.add_argument("id")
    create.add_argument("--name")
    create.add_argument("--role")
    create.add_argument("--capability", action="append", type=_parse_capability, default=[],
                         metavar="id:strength", help="repetible, p. ej. --capability finance:primary")
    create.add_argument("--extends", default=None, help="id de una plantilla en agents/_base/")
    create.add_argument("--profile", default="reasoning_std")
    create.add_argument("--priority", type=int, default=5)
    create.add_argument("--source", action="append", default=[], help="namespace de knowledge/, repetible")
    create.add_argument("--policy", default="analyst_base")
    create.add_argument("--force", action="store_true")
    create.set_defaults(func=cmd_agent_create)

    validate = agent_sub.add_parser("validate", help="valida un agente (estructural + referencial)")
    validate.add_argument("id")
    validate.set_defaults(func=cmd_agent_validate)

    activate = agent_sub.add_parser("activate", help="draft → active (tras validar)")
    activate.add_argument("id")
    activate.set_defaults(func=cmd_agent_activate)

    deprecate = agent_sub.add_parser("deprecate", help="active → deprecated")
    deprecate.add_argument("id")
    deprecate.set_defaults(func=cmd_agent_deprecate)

    explain = agent_sub.add_parser("explain", help="por qué (no) matchea esta query")
    explain.add_argument("id")
    explain.add_argument("query")
    explain.set_defaults(func=cmd_agent_explain)

    test = agent_sub.add_parser("test", help="corre examples.md como casos de contrato")
    test.add_argument("id")
    test.set_defaults(func=cmd_agent_test)

    capability = sub.add_parser("capability", help="gestión del catálogo de capacidades")
    capability_sub = capability.add_subparsers(dest="action", required=True)

    cap_create = capability_sub.add_parser("create", help="crea una nueva Capability")
    cap_create.add_argument("id")
    cap_create.add_argument("--name")
    cap_create.add_argument("--description")
    cap_create.add_argument("--parent", default=None)
    cap_create.add_argument("--example", action="append", default=[])
    cap_create.add_argument("--force", action="store_true")
    cap_create.set_defaults(func=cmd_capability_create)

    cap_list = capability_sub.add_parser("list", help="lista capacidades y qué agentes las implementan")
    cap_list.set_defaults(func=cmd_capability_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
