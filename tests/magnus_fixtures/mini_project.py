"""Construye un proyecto Magnus completo y mínimo en un directorio temporal.

Un proyecto Magnus no es un solo archivo: es `agents/` + `capabilities/` +
`configs/` + `tools/` + la wiki. Los tests necesitan las cinco piezas
coherentes entre sí (el Agent Registry valida referencialmente contra todas),
así que en vez de comprometer 30 archivos casi vacíos al repositorio, se
generan aquí en `tmp_path`. Son archivos reales en disco — el registro los lee
igual que leería los de producción — pero viven solo mientras dura el test.

Contenido:
  - 2 capacidades: `finanzas_test`, `sueno_test`.
  - 1 base de herencia `_base/test_base` con los 12 Markdown obligatorios.
  - 3 agentes activos:
      * `fina`   — finanzas, wiki con evidencia real, require_citations=true
      * `dormi`  — sueño, wiki con evidencia real, require_citations=false
      * `vacio`  — apunta a un namespace SIN notas, require_citations=true
        (es el agente con el que se prueba la política de evidencia del paso 3)
  - wiki con 2 namespaces poblados y 1 vacío.
"""
from __future__ import annotations

from pathlib import Path

REQUIRED_MD_FILES = [
    "identity.md", "mission.md", "personality.md", "principles.md",
    "skills.md", "knowledge.md", "tools.md", "permissions.md",
    "memory.md", "examples.md", "evaluation.md", "workflows.md",
]

# --- capacidades -------------------------------------------------------------
_CAPABILITIES = {
    "finanzas_test": """
id: finanzas_test
name: "Finanzas (test)"
description: >
  Inflación, tasas, banco central y precios.
routing_examples:
  - "cuál es la inflación en República Dominicana"
  - "qué hace el banco central con las tasas"
""",
    "sueno_test": """
id: sueno_test
name: "Sueño (test)"
description: >
  Descanso, higiene del sueño y horarios de dormir.
routing_examples:
  - "cómo mejorar mi sueño"
  - "cuántas horas debo dormir"
""",
}

# --- base de herencia --------------------------------------------------------
_BASE_YAML = """
tools:
  allow: [llm_wiki]
  deny:  [email, calendar, terminal]

permissions:
  policy_ref: test_readonly

evaluation:
  rigor: 8
  require_citations: true

constitution:
  inherits: [magnus_constitution, evidence_policy]
"""

# --- agentes -----------------------------------------------------------------
_AGENTS = {
    "fina": """
mas_version: "1.0"
id: fina
name: "Fina"
role: "Agente de finanzas para pruebas"
version: "1.0.0"
status: active
extends: test_base

routing:
  capabilities:
    - { id: finanzas_test, strength: primary }
  priority: 7

knowledge:
  sources: [01-Finanzas]
  retrieval: { top_k: 4, min_score: 0.30 }

model:
  profile: perfil_test
  fallback_profile: perfil_barato

evaluation:
  rubric_ref: finanzas_strict
  require_citations: true
""",
    "dormi": """
mas_version: "1.0"
id: dormi
name: "Dormi"
role: "Agente de sueño para pruebas"
version: "1.0.0"
status: active
extends: test_base

routing:
  capabilities:
    - { id: sueno_test, strength: primary }
  priority: 6

knowledge:
  sources: [02-Sueno]
  retrieval: { top_k: 3, min_score: 0.10 }

model:
  profile: perfil_barato

evaluation:
  rubric_ref: sueno_std
  require_citations: false
""",
    "vacio": """
mas_version: "1.0"
id: vacio
name: "Vacio"
role: "Agente cuyo namespace no tiene notas — prueba la política de evidencia"
version: "1.0.0"
status: active
extends: test_base

routing:
  capabilities:
    - { id: finanzas_test, strength: secondary }
  priority: 1

knowledge:
  sources: [99-Namespace-Sin-Notas]
  retrieval: { top_k: 4, min_score: 0.30 }

model:
  profile: perfil_test

evaluation:
  rubric_ref: finanzas_strict
  require_citations: true
""",
}

# --- configuración -----------------------------------------------------------
_MODELS_YAML = """
profiles:
  perfil_test:
    primary:  { provider: fake, model: "fake-grande", temperature: 0.0 }
    fallback: { provider: fake_alterno, model: "fake-respaldo" }

  perfil_barato:
    primary:  { provider: fake, model: "fake-pequeno" }

  perfil_sin_adaptador:
    primary:  { provider: proveedor_inexistente, model: "no-existe" }

providers:
  fake:         { api_key_env: FAKE_API_KEY }
  fake_alterno: { api_key_env: FAKE_ALT_API_KEY }

embeddings:
  default: { provider: local, model: "bge-m3", dim: 1024 }
"""

_PERMISSIONS_YAML = """
policies:
  test_readonly:
    knowledge:
      read:  ["01-Finanzas/", "02-Sueno/", "99-Namespace-Sin-Notas/"]
      write: []
    tools:
      allow: [llm_wiki, python]
      deny:  [terminal, email]
    actions:
      external_side_effects: require_human_approval

  # Política deliberadamente desalineada con lo que declaran los agentes:
  # sirve para probar que el enforcement deniega en vez de dejar pasar.
  test_sin_lectura:
    knowledge:
      read:  ["77-Namespace-Que-Nadie-Usa/"]
      write: []
    tools:
      allow: []
      deny:  [llm_wiki]
    actions:
      external_side_effects: deny

roles:
  admin:    { can: ["*"] }
  operator: { can: [query, agents.read] }
  tecnico:  { can: [query, tools.use] }
"""

# Los adaptadores de test se llaman `fake` (remoto) y `fake_local` (local), así
# que la política de egreso puede distinguirlos igual que anthropic vs ollama.
_PRIVACY_YAML = """
egreso:
  por_defecto: local_only
  proveedores_locales: [fake_local, ollama]
  namespaces:
    01-Finanzas: remote_allowed
    02-Sueno: remote_allowed
    99-Namespace-Sin-Notas: remote_allowed
"""

_MCP_CATALOG_YAML = """
servers:
  llm_wiki:
    scopes: [read_only]
"""

_GUARDRAILS_YAML = """
dominios:
  finanzas:
    capacidades: [finanzas_test]
    aviso: "Esto no es asesoría de inversión (aviso de prueba)."
  salud:
    capacidades: [sueno_test]
    aviso: "Esto no es una indicación médica (aviso de prueba)."

urgencias:
  - id: crisis_prueba
    patrones:
      - "quiero hacerme dano"
      - "senal de urgencia"
    respuesta: "Escalado de prueba: esto necesita atención humana, no tus notas."

  - id: urgencia_solo_salud
    capacidades: [sueno_test]
    patrones:
      - "llevo cinco dias sin dormir nada"
    respuesta: "Escalado de prueba limitado al dominio de salud."
"""

# --- wiki --------------------------------------------------------------------
# Texto denso en términos para que el retriever léxico supere `min_score` sin
# depender de la wiki real del usuario.
_WIKI = {
    "01-Finanzas/Inflacion RD.md": """---
tags: [economia]
---

# Inflación en República Dominicana

La inflación interanual en República Dominicana cerró en 3.4 por ciento.
La inflación se mantiene dentro de la meta del Banco Central de la República
Dominicana, fijada en 4 por ciento con un rango de tolerancia de un punto.

# Tasa de política monetaria

El Banco Central fijó la tasa de política monetaria en 5.75 por ciento.
Una tasa más alta enfría el crédito y contiene la inflación.
""",
    "01-Finanzas/Presupuesto personal.md": """# Presupuesto personal

Un presupuesto personal asigna cada peso del ingreso mensual a una categoría
de gasto antes de que empiece el mes. El ahorro se trata como un gasto fijo.
""",
    "02-Sueno/Higiene del sueno.md": """# Higiene del sueño

Dormir entre siete y nueve horas mejora la consolidación de la memoria.
La higiene del sueño incluye horarios estables para dormir y despertar,
oscuridad total en la habitación y evitar pantallas una hora antes de dormir.
""",
}


def build_mini_project(root: Path) -> Path:
    """Escribe el proyecto completo bajo `root` y devuelve `root`."""
    root = Path(root)

    caps = root / "capabilities"
    caps.mkdir(parents=True, exist_ok=True)
    for cap_id, text in _CAPABILITIES.items():
        (caps / f"{cap_id}.capability.yaml").write_text(text.strip() + "\n", encoding="utf-8")

    base = root / "agents" / "_base" / "test_base"
    base.mkdir(parents=True, exist_ok=True)
    (base / "agent.yaml").write_text(_BASE_YAML.strip() + "\n", encoding="utf-8")
    for filename in REQUIRED_MD_FILES:
        title = filename.removesuffix(".md")
        (base / filename).write_text(
            f"# {title}\n\nContenido base heredado para pruebas.\n", encoding="utf-8")

    for agent_id, text in _AGENTS.items():
        d = root / "agents" / agent_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "agent.yaml").write_text(text.strip() + "\n", encoding="utf-8")

    configs = root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "models.yaml").write_text(_MODELS_YAML.strip() + "\n", encoding="utf-8")
    (configs / "permissions.yaml").write_text(_PERMISSIONS_YAML.strip() + "\n", encoding="utf-8")
    (configs / "guardrails.yaml").write_text(_GUARDRAILS_YAML.strip() + "\n", encoding="utf-8")
    (configs / "privacy.yaml").write_text(_PRIVACY_YAML.strip() + "\n", encoding="utf-8")

    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "mcp_catalog.yaml").write_text(_MCP_CATALOG_YAML.strip() + "\n", encoding="utf-8")

    wiki = root / "LLM-Wiki" / "wiki"
    for rel, text in _WIKI.items():
        p = wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    # namespace declarado por `vacio` que existe pero no tiene notas
    (wiki / "99-Namespace-Sin-Notas").mkdir(parents=True, exist_ok=True)

    return root
