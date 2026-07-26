# MAS — Magnus Agent Specification v1.0

> Un estándar abierto para definir agentes, al nivel conceptual de MCP, OpenAPI o JSON Schema.
> **Objetivo:** que crear un agente sea *copiar una carpeta y editarla*.

---

## 1. Filosofía

MAS separa **qué es** un agente (declarativo, en Markdown/YAML legible por humanos) de **cómo se ejecuta** (el runtime de Magnus). Un agente MAS es portable: cualquier runtime que implemente la especificación puede cargarlo.

Un principio: **el agente describe capacidades e intención, nunca conocimiento factual**. Los hechos viven en `knowledge/`.

## 2. Estructura de un agente

```
agents/ernesto_libras/
├── agent.yaml         # Manifiesto — metadatos + routing (obligatorio)
├── identity.md        # Rol, objetivo, especialidades, rigor
├── mission.md         # Misión y alcance
├── personality.md     # Estilo, tono, límites de expresión
├── principles.md      # Reglas de razonamiento y ética específica
├── skills.md          # Capacidades declaradas (para el Router)
├── tools.md           # Herramientas MCP permitidas
├── permissions.md     # Qué puede leer/ejecutar (referencia a permissions.yaml)
├── knowledge.md       # Rutas de knowledge/ que consulta por defecto
├── memory.md          # Política de memoria del agente
├── workflows.md       # Procedimientos paso a paso por tipo de tarea
├── examples.md        # Ejemplos few-shot de comportamiento correcto
├── evaluation.md      # Rúbrica con la que el Evaluador juzga sus respuestas
├── memory/            # Memoria local del agente (opcional, gestionada por runtime)
└── prompts/           # Plantillas de prompt específicas (opcional)
```

> Crear un agente nuevo = `cp -r agents/_template agents/nuevo_agente` y editar.

## 3. `agent.yaml` — manifiesto (contrato mínimo)

Es el único archivo obligatorio que el runtime *parsea* estructuralmente; los `.md` son contexto que se inyecta.

```yaml
mas_version: "1.0"
id: ernesto_libras
name: "Ernesto Libras"
role: "Economista Senior de Magnus Dynamic Group"
version: "1.3.0"
status: active            # active | draft | deprecated

routing:
  domains: [economics, macroeconomics, markets, finance, risk]
  # frases de ejemplo para el índice semántico del Router
  intents:
    - "proyección económica"
    - "análisis de mercado"
    - "política monetaria"
  priority: 8             # desempate cuando varios agentes matchean

knowledge:
  # rutas de la LLM Wiki que este agente consulta por defecto
  sources:
    - economics/
    - finance/
    - world_bank/
    - imf/
    - federal_reserve/
    - oecd/
  retrieval:
    top_k: 8
    reranker: true
    min_score: 0.35

model:
  # NO fija un proveedor: fija un "perfil" resuelto en models.yaml
  profile: reasoning_high        # p.ej. mapea a claude-opus-4-8, gpt-*, etc.
  fallback_profile: reasoning_std
  effort: high                   # low|medium|high|xhigh|max (si el proveedor lo soporta)

tools:
  allow: [kiwix, llm_wiki, magnus_capital, python, terminal, imf_api, world_bank_api, fred]
  deny: [email, calendar]        # explícitamente prohibidas

permissions:
  policy_ref: economist_readonly # definido en configs/permissions.yaml

memory:
  short_term: true
  episodic: true
  semantic_write: proposal_only  # el agente PROPONE; humano aprueba (P7)

evaluation:
  rubric_ref: evidence_strict
  rigor: 10                      # 0-10; exige nivel de confianza si hay incertidumbre
  require_citations: true

constitution:
  inherits: [magnus_constitution, evidence_policy, citation_policy]
```

### Campos y su significado

| Campo | Uso en runtime |
|-------|----------------|
| `routing.*` | Alimenta el índice semántico del **Router**. |
| `knowledge.sources` | Filtro de namespace del **RAG** (el agente solo ve su parcela). |
| `model.profile` | Se resuelve en `models.yaml` → adaptador concreto. **Nunca** un modelo hardcodeado aquí. |
| `tools.allow/deny` | Lista blanca/negra aplicada por el **Sistema de herramientas** y **Permisos**. |
| `permissions.policy_ref` | Enlaza con una política del contexto de Governance. |
| `memory.semantic_write` | Encarna P7: `proposal_only` = el agente nunca escribe conocimiento sin aprobación humana. |
| `evaluation.*` | Rúbrica que aplica el **Evaluador**. |

## 4. Ejemplos de los `.md`

### `identity.md`
```markdown
# Ernesto Libras
Rol: Economista Senior de Magnus Dynamic Group.
Objetivo: proporcionar análisis económicos usando ÚNICAMENTE evidencia verificable.

Especialidades: Macroeconomía · Política Monetaria · Finanzas · Mercados · Riesgo.
Debe evitar: opiniones sin evidencia, especulación, sesgos políticos.
Siempre citar fuentes. Nivel de rigurosidad: 10/10.
Cuando exista incertidumbre, indicar el nivel de confianza.
```

### `personality.md`
```markdown
Calmado. Objetivo. Muy analítico. Nunca exagera.
Habla como un economista senior. Evita emociones.
Usa gráficos cuando sea posible. Siempre compara escenarios.
```

### `tools.md`
```markdown
Puede utilizar: Kiwix · LLM Wiki · Magnus Capital · Python · Terminal ·
IMF API · World Bank API · FRED · (futuro) MCP Bloomberg.
```

### `workflows.md`
```markdown
## Proyección económica
1. Buscar información (RAG sobre knowledge/economics, finance, imf…).
2. Evaluar calidad de la evidencia.
3. Comparar evidencia entre fuentes.
4. Generar hipótesis.
5. Simular escenarios.
6. Entregar conclusión.
7. Indicar incertidumbre (nivel de confianza explícito).
```

### `evaluation.md`
```markdown
Rúbrica (0-100):
- Evidencia citada y trazable ............... 35
- Corrección factual vs RAG ................. 25
- Escenarios comparados ..................... 15
- Incertidumbre declarada ................... 15
- Ausencia de sesgo / especulación .......... 10
Umbral de publicación: 80. Por debajo → reintento o escalado a humano.
```

## 5. Ciclo de vida de un agente MAS

```
draft ──(review humano)──► active ──(nueva versión)──► active(vN+1)
                                   └──(obsoleto)──────► deprecated
```

- Cada cambio en `agent.yaml` incrementa `version` (SemVer).
- El **Registro de agentes** valida el manifiesto contra el JSON Schema de MAS antes de activar.
- `deprecated` sigue respondiendo tareas ya enrutadas pero no recibe tráfico nuevo.

## 6. Validación

`agent.yaml` se valida contra `mas.schema.json` (JSON Schema Draft 2020-12). Reglas duras:

- `model.profile` debe existir en `models.yaml`.
- Toda ruta en `knowledge.sources` debe existir en `knowledge/`.
- Toda herramienta en `tools.allow` debe estar registrada en `tools/`.
- `permissions.policy_ref` debe existir en `permissions.yaml`.
- Si `evaluation.require_citations: true`, el Evaluador rechaza respuestas sin citas.

Un manifiesto inválido **no se activa**: falla rápido en el arranque/registro, no en tiempo de consulta.

## 7. Herencia y composición

Los agentes pueden heredar de una plantilla base para reducir duplicación:

```yaml
extends: _base/analyst        # hereda personality, principles, evaluation
overrides:
  routing.domains: [economics]
```

Esto habilita "familias" de agentes (analistas, redactores, revisores) manteniendo un único punto de verdad para lo común — clave para escalar a **cientos de agentes**.
