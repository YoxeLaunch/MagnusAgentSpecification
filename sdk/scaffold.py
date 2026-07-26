"""Plantillas de esqueleto MAS — usadas por `magnus agent create`.

Cada archivo trae un comentario `<!-- completa: ... -->` que
`AgentRegistry` (ver orchestration/registry/agent_registry.py) exige
eliminar antes de poder activar el agente (`status: active`). Esto es lo
que convierte "crear un agente" en "lo que el SDK genera correctamente por
defecto", en vez de un documento que hay que leer con cuidado
(docs/04-MAGNUS-V2-ARQUITECTURA.md §13).
"""
from __future__ import annotations


def agent_yaml(agent_id: str, name: str, role: str, capabilities: list[tuple[str, str]],
               extends: str | None, profile: str, priority: int, sources: list[str],
               policy_ref: str) -> str:
    caps_yaml = "\n".join(f"    - {{ id: {cid}, strength: {strength} }}" for cid, strength in capabilities)
    sources_yaml = "\n".join(f"    - {s}" for s in sources) if sources else "    []"
    extends_line = f"extends: {extends}\n" if extends else ""
    return f"""mas_version: "1.0"
id: {agent_id}
name: "{name}"
role: "{role}"
version: "0.1.0"
status: draft
{extends_line}
routing:
  capabilities:
{caps_yaml}
  priority: {priority}

knowledge:
  sources:
{sources_yaml}
  retrieval:
    top_k: 8
    reranker: true
    min_score: 0.35

model:
  profile: {profile}
  fallback_profile: routing_fast

tools:
  allow: [llm_wiki]
  deny:  [terminal, email, calendar]

permissions:
  policy_ref: {policy_ref}

memory:
  short_term: true
  episodic: true
  semantic_write: proposal_only

evaluation:
  rubric_ref: evidence_strict
  rigor: 8
  require_citations: true

constitution:
  inherits: [magnus_constitution, evidence_policy, citation_policy]
"""


_MD_PLACEHOLDERS: dict[str, str] = {
    "identity.md": """# {name}

Rol: {role}.

<!-- completa: objetivo del agente en 1-2 frases -->

## Especialidades
<!-- completa: lista de especialidades -->

## Debe evitar
<!-- completa: qué NO debe hacer este agente -->

Nivel de rigurosidad: <!-- completa: 0-10 -->.
""",
    "mission.md": """# Misión — {name}

<!-- completa: propósito de negocio de este agente en 2-3 frases -->

## A quién sirve
<!-- completa -->

## Límites de alcance
<!-- completa: qué queda explícitamente fuera de su misión -->
""",
    "personality.md": """<!-- completa: tono, estilo, límites de expresión de {name} -->
""",
    "principles.md": """# Principios de razonamiento — {name}

Además de la constitución de Magnus (`constitution/magnus_constitution.md`):

1. <!-- completa: principio propio 1 -->
2. <!-- completa: principio propio 2 -->
""",
    "skills.md": """# Skills — {name}

> Semilla del índice de capacidades. Mantener alineado con
> `agent.yaml::routing.capabilities`.

<!-- completa: capacidades primarias/secundarias, ejemplos de tareas que resuelve bien y lo que NO hace -->
""",
    "knowledge.md": """# Knowledge — {name}

## Namespaces por defecto
<!-- completa: namespaces de la wiki, alineados con agent.yaml::knowledge.sources -->

## Justificación
<!-- completa: por qué esta parcela y no otra (mínimo privilegio) -->
""",
    "tools.md": """# Tools — {name}

## Permitidas
<!-- completa -->

## Explícitamente denegadas
<!-- completa -->
""",
    "permissions.md": """# Permissions — {name}

Referencia: `configs/permissions.yaml::policies.<!-- completa: policy_ref -->`.

<!-- completa: qué puede leer/escribir/ejecutar este agente -->
""",
    "memory.md": """# Memory — {name}

| Tipo | Uso |
|---|---|
| Short term | <!-- completa --> |
| Episodic | <!-- completa --> |
| Project Memory | <!-- completa: ¿lo necesita? --> |
| Semantic | Solo `proposal_only`. |
""",
    "examples.md": """# Examples — {name}

## Correcto
<!-- completa: un ejemplo de comportamiento correcto -->

## Incorrecto (a evitar)
<!-- completa: un ejemplo de comportamiento incorrecto y qué principio viola -->
""",
    "evaluation.md": """# Rúbrica de evaluación — {name}

Puntuación (0-100):
<!-- completa: criterios y pesos -->

Umbral de publicación: <!-- completa -->.
""",
    "workflows.md": """# Workflows de {name}

## <!-- completa: nombre del flujo principal -->
1. <!-- completa -->
""",
}


def markdown_files(name: str, role: str) -> dict[str, str]:
    return {fname: tmpl.format(name=name, role=role) for fname, tmpl in _MD_PLACEHOLDERS.items()}


def capability_yaml(cap_id: str, name: str, description: str, parent: str | None,
                     examples: list[str]) -> str:
    examples_yaml = "\n".join(f"  - \"{e}\"" for e in examples) if examples else "  - \"<!-- completa: frase de ejemplo -->\""
    parent_line = parent if parent else "null"
    return f"""id: {cap_id}
name: "{name}"
parent: {parent_line}
description: >
  {description}
routing_examples:
{examples_yaml}
embedding_seed: auto
"""
