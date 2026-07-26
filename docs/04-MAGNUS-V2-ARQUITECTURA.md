# Magnus V2 — Arquitectura Definitiva (Reconciliación)

> Objetivo de este documento: cerrar la brecha detectada en la auditoría entre lo
> **documentado** (`00-VISION-Y-ARQUITECTURA.md`, `01-MAS-especificacion.md`,
> `02-COMPONENTES.md`) y lo que **corre de verdad** (`orchestration/engine.py` con
> 4 de 5 agentes hardcodeados y un router por keywords). Este documento es la
> versión **normativa y única** a partir de ahora: donde contradiga a los docs
> 00–03, este documento manda. No contiene código de implementación — contratos
> (`Protocol`) donde el propio proyecto ya usa ese estilo como especificación.

Analogía de diseño, explícita porque ordena cada decisión de abajo:

| Referencia | Equivalente en Magnus |
|---|---|
| Kubernetes | **Agent Registry** + **Capability Engine** (orquesta *qué corre dónde*, con salud, versión y ciclo de vida) |
| Docker / OCI image | **Carpeta MAS de un agente** (`agent.yaml` + Markdown = la "imagen" portable) |
| OpenAPI | **`agent.schema.json`** (contrato validable, generación de herramientas/SDK a partir de él) |
| MCP | **Integración MCP ya existente** (herramientas), ahora declarada 1:1 por agente, sin excepciones |
| Helm / scaffolding CLI | **Magnus Agent SDK** (`magnus agent create`) |

---

## 1. Principio de reconciliación: MAS es la única fuente de verdad

**Regla dura:** ningún componente del runtime puede definir un agente en código.
Si un agente no tiene una carpeta en `agents/<id>/` con un `agent.yaml` válido,
**no existe** para el sistema. Punto.

### Qué se elimina

| Elemento actual | Acción | Motivo |
|---|---|---|
| `orchestration/engine.py::default_config()` (agentes `serena`, `amanda`, `dr_soma`, `lexi` hardcodeados como `AgentDef`) | **Eliminar por completo** | Segunda fuente de verdad paralela a `agents/*` |
| `orchestration/engine.py::_route()` (matching por intersección de keywords) | **Eliminar** | Sustituido por Capability Engine (§3) |
| `MagnusEngine` como orquestador monolítico | **Descomponer** en `AgentRegistry` + `CapabilityEngine` + `Router` (ya diseñado en `orchestration/router.py`) + `RAGPipeline` (ya existe) | Cada responsabilidad ya tiene un componente propio en `02-COMPONENTES.md`; `engine.py` los duplicaba en un solo archivo de forma ad-hoc |
| Dos routers distintos (`router.py` semántico vs `engine.py` keywords) | **Uno solo**: el semántico, ahora respaldado por capacidades (no por `identity.md` en bruto) | Evita que "el que documentamos" y "el que corre" diverjan otra vez |

### Qué se conserva (ya está bien hecho)

- `providers/base.py`, `providers/registry.py` (puerto `LLMProvider`, sin cambios de fondo).
- `kernel/rag/pipeline.py` (pipeline RAG con citas, sin cambios de fondo).
- `agents/ernesto_libras/` como agente (se completa, no se reescribe desde cero).
- `mcp_server/` como transporte (se le conecta al Registry real, ver §8).

### Contrato de arranque

El runtime, al iniciar, ejecuta siempre esta secuencia y **falla rápido** si algo
no valida (nunca en tiempo de consulta):

```
1. AgentRegistry.load_all()          # descubre agents/*, valida contra agent.schema.json
2. AgentRegistry.resolve_inheritance()  # aplica cadenas extends (§6)
3. AgentRegistry.build_capability_index()  # embeddings de skills.md + routing.capabilities
4. CapabilityEngine.warm_cache()     # precalienta índice para el Router
5. ToolRegistry.bind(agent) por cada agente activo  # valida tools.allow contra MCP servers declarados
```

Si un `agent.yaml` no valida, ese agente queda en `status: invalid` (visible en
el Dashboard/CLI) y **no recibe tráfico** — pero no tira abajo el arranque de
los demás. Esto es distinto de hoy: hoy no hay validación alguna, así que
tampoco hay forma de fallar limpio.

---

## 2. Agent Registry

**Responsabilidad única:** es el *kubelet* de Magnus. Ningún otro componente
descubre agentes tocando el filesystem — todos hablan con el Registry.

```python
class AgentRegistry(Protocol):
    def load_all(self) -> LoadReport: ...
    def get(self, agent_id: str, version: str | None = None) -> AgentSpec: ...
    def list(self, *, status: AgentStatus | None = None) -> list[AgentSpec]: ...
    def validate(self, spec_dir: Path) -> ValidationResult: ...
    def activate(self, agent_id: str, version: str) -> None: ...
    def deprecate(self, agent_id: str, version: str) -> None: ...
    def capabilities_of(self, agent_id: str) -> list[Capability]: ...
    def tools_of(self, agent_id: str) -> list[ToolSpec]: ...
    def permissions_of(self, agent_id: str) -> PolicyRef: ...
    def reload(self, agent_id: str) -> AgentSpec: ...   # hot-reload de una sola definición
```

### Ciclo de vida (igual al ya documentado, ahora con dueño único)

```
draft ──(validate + review humano)──► active ──(nueva version, SemVer)──► active(vN+1)
                                              └──(deprecated)────────────► deprecated
```

- `activate` exige: `agent.yaml` válido contra `agent.schema.json` + herencia
  resuelta sin ciclos + herramientas declaradas registradas en el catálogo MCP
  + `permissions.policy_ref` existente.
- `deprecated` sigue respondiendo tareas ya enrutadas (compatibilidad hacia
  atrás para conversaciones en curso) pero desaparece del índice de
  capacidades → no recibe tráfico nuevo.
- Cada cambio de `agent.yaml` sube `version` (SemVer). El Registry conserva
  N versiones anteriores para auditoría/rollback (ligado a §14 del doc 02,
  Versionado — aquí aplicado a agentes, no solo a `knowledge/`).

### Lo que registra (más allá de "cargar el YAML")

| Sub-registro | Contenido | Consumidor |
|---|---|---|
| **Capability registry** | Qué `Capability` declara y con qué fuerza (`primary` / `secondary`) | Capability Engine (§3) |
| **Tool registry** | Qué MCP servers/herramientas puede invocar | Sistema de herramientas (§8) |
| **Permission registry** | Qué `policy_ref` aplica, resuelto y aplanado (tras herencia) | Policy Engine |
| **Knowledge scope registry** | Namespaces de `knowledge/` que puede consultar | RAG (filtro obligatorio) |
| **Version registry** | Historial de versiones activas/deprecadas por agente | Auditoría, rollback |

### Almacenamiento e índices (preparación para §7)

- **Fuente canónica:** el filesystem (`agents/*`), igual que Kubernetes usa
  manifiestos declarativos como fuente de verdad.
- **Caché caliente:** `AgentSpec` compilado (YAML + Markdown + herencia
  resuelta) en memoria de proceso, invalidado por `reload()` o *file watcher*.
- **Índice de capacidades:** vector index (embeddings de `skills.md` +
  `routing.capabilities` + ejemplos de `examples.md`) — es lo que usa el
  Capability Engine, **no** el Registry directamente.
- **Metadatos/versiones:** Postgres (o SQLite embebido en instalaciones
  pequeñas) — solo metadatos de ciclo de vida, nunca conocimiento.

---

## 3. Capability Engine (reemplaza el routing por keywords)

### Por qué keywords no escala

El `_route()` actual hace `set(keywords) ∩ set(query_tokens)`. Con 5 agentes
funciona por casualidad. Con 500, colisiona: cualquier agente con la palabra
"salud" en su set gana igual de fácil que uno realmente especializado, no hay
noción de fuerza de la capacidad, no hay *matching* semántico (sinónimos,
paráfrasis, otros idiomas) y no hay forma de que dos agentes compartan una
capacidad con distinta profundidad (p. ej. "Finanzas" como capacidad principal
de Ernesto vs. secundaria de un futuro agente de "Brand Strategy" que necesita
hablar de presupuesto de marketing).

### Modelo de capacidades

Una **Capability** es una entidad de primer nivel, independiente del agente
que la implemente — exactamente como un `CRD` en Kubernetes es independiente
del Pod que lo satisface:

```yaml
# capabilities/finance.capability.yaml
id: finance
name: "Finance"
parent: null                     # las capacidades también forman jerarquía
description: >
  Análisis financiero, presupuesto, inversión, riesgo de portafolio.
routing_examples:
  - "¿debería invertir en bonos o acciones?"
  - "análisis de riesgo de mi portafolio"
embedding_seed: auto             # se deriva de description + routing_examples
```

```yaml
# capabilities/macroeconomics.capability.yaml
id: macroeconomics
parent: finance
name: "Macroeconomics"
description: "Política monetaria, inflación, ciclos económicos, bancos centrales."
```

Un agente **declara** capacidades, no dominios sueltos:

```yaml
# agents/ernesto_libras/agent.yaml (fragmento)
routing:
  capabilities:
    - { id: macroeconomics, strength: primary }
    - { id: finance,        strength: primary }
    - { id: markets,        strength: secondary }
  priority: 8
```

### Cómo enruta el Capability Engine

```python
class CapabilityEngine(Protocol):
    def match(self, query: str, *, k: int = 3, min_score: float = 0.35) -> list[CapabilityMatch]: ...
    def agents_for(self, capability_id: str) -> list[AgentRef]: ...
    def explain(self, query: str, agent_id: str) -> MatchExplanation: ...   # auditable, no caja negra
```

Flujo:

```
query → embedding(query) → similitud contra índice de Capability (no de Agent)
      → top-k capacidades por encima de min_score
      → para cada capacidad: agentes que la declaran, ordenados por
        (strength=primary > secondary) y luego por priority
      → RoutePlan{agents, mode, capabilities_matched, reason}   # reason auditable
```

La diferencia clave con hoy: el índice vectorial se construye sobre
**capacidades** (pocas, estables, curadas), y los agentes se resuelven como un
segundo salto (`capability → agentes`). Esto separa dos preguntas que hoy están
mezcladas: *"¿de qué trata esto?"* (capacidad) y *"¿quién lo atiende?"*
(agente) — la misma separación que MCP hace entre *tool* y *server*, o que
Kubernetes hace entre `Service` (capacidad expuesta) y `Pod` (quién la sirve).

Esto habilita, sin cambiar el motor:
- **Múltiples agentes por capacidad** → balanceo/especialización (p. ej. dos
  agentes de "Programming", uno para revisión de seguridad y otro para
  arquitectura, ambos con `strength: primary`).
- **Un agente, muchas capacidades** con distinta fuerza.
- **Capacidades jerárquicas** (`macroeconomics` hereda relevancia de
  `finance`) sin duplicar `routing_examples`.

`Router` (ya diseñado en `orchestration/router.py`) pasa a consumir
`CapabilityEngine.match()` en vez de `AgentRegistry.search()` directo sobre
texto libre; el resto de `router.py` (modo single/parallel/sequential, merge)
**no cambia** — ya estaba bien diseñado, solo le faltaba un motor de matching
serio debajo.

---

## 4. Agentes oficiales — plantilla completa y capacidades asignadas

Los 12 archivos por agente (`identity.md`, `mission.md`, `personality.md`,
`principles.md`, `skills.md`, `knowledge.md`, `tools.md`, `permissions.md`,
`memory.md`, `examples.md`, `evaluation.md`, `workflows.md`) dejan de ser
"opcionales salvo `agent.yaml`" y pasan a ser **obligatorios para `status:
active`** — es la diferencia entre un manifiesto mínimo (`draft`) y uno listo
para producción. `agent.schema.json` (§5) lo exige estructuralmente para
`draft→active`.

### Tabla de contenido por archivo (contrato, no relleno)

| Archivo | Debe responder | Lo usa |
|---|---|---|
| `identity.md` | Quién es, objetivo, qué evita | Prompt de sistema |
| `mission.md` | Propósito de negocio, a quién sirve, límites de alcance | Router (desambiguación), Dashboard |
| `personality.md` | Tono, estilo, límites de expresión | Prompt de sistema |
| `principles.md` | Reglas de razonamiento propias, además de la constitución | Prompt de sistema, Evaluador |
| `skills.md` | Capacidades declaradas en lenguaje natural + ejemplos | **Semilla del índice de capacidades** |
| `knowledge.md` | Namespaces por defecto, con justificación | RAG (scope) |
| `tools.md` | Herramientas MCP permitidas, con justificación de cada una | ToolRegistry, Permisos |
| `permissions.md` | Referencia legible a `permissions.yaml`, explicada | Auditoría/Dashboard |
| `memory.md` | Qué tipos de memoria usa y con qué política de escritura | Memory Engine (§9) |
| `examples.md` | Few-shot de comportamiento correcto **e incorrecto** | Evaluador (calibración), tests de contrato |
| `evaluation.md` | Rúbrica cuantitativa + umbral de publicación | Evaluador |
| `workflows.md` | Procedimiento paso a paso por tipo de tarea | Planner/Delegation |

### Asignación de capacidades (reemplaza `routing.domains` por keywords)

| Agente | Capacidades `primary` | Capacidades `secondary` | Namespace `knowledge/` |
|---|---|---|---|
| **Ernesto Libras** | `macroeconomics`, `finance` | `markets`, `risk` | `01-Economia-y-Finanzas/` |
| **Serena** | `mental_health` | `personal_development` | `03-Salud-Mental-y-Desarrollo-Personal/` |
| **Amanda** | `project_management` | `productivity`, `career_transition` | `05-Proyectos-y-Planes/` |
| **Dr. Soma** | `medicine` (alcance: bienestar/estilo de vida, **no** diagnóstico clínico — ver `principles.md`) | `nutrition`, `fitness` | `02-Salud-Corporal/` |
| **Lexi** | `programming`, `ai_ml` | `technology_strategy` | `06-Tecnologia-e-IA/` |

`Brand Strategy` (mencionada en el objetivo del usuario como ejemplo) queda
definida como capacidad en el catálogo (`capabilities/brand_strategy.capability.yaml`)
pero **sin agente propio todavía** — el Capability Engine debe degradar con
gracia (respuesta "sin agente para esta capacidad" en vez de forzar un match
falso), igual que Kubernetes deja un `Service` sin `Endpoints` en vez de
inventar un Pod.

### Construcción de los 5 agentes: quién y cuándo

Objetivo 4 ("construir completamente" los 5 agentes) es trabajo de
**contenido**, no de arquitectura — pertenece a Fase 2 del roadmap (§12), una
vez exista `agent.schema.json` (Fase 1) para validar cada archivo a medida que
se escribe. Escribirlos antes del schema es repetir el problema de hoy: 60
archivos sin ningún mecanismo que garantice que siguen la especificación.

---

## 5. `agent.schema.json`

**Rol:** el equivalente a OpenAPI para Magnus — todo `agent.yaml` se valida
contra él antes de `activate()`. JSON Schema Draft 2020-12, igual que ya
propone `01-MAS-especificacion.md` (que documentaba esto pero nunca lo
materializó como archivo).

### Reglas duras que el schema codifica

| Regla | Nivel |
|---|---|
| `mas_version`, `id`, `name`, `role`, `version` (SemVer), `status` ∈ {draft, active, deprecated} | Estructural (`required`) |
| `routing.capabilities[].id` debe existir en `capabilities/*.capability.yaml` | Referencial (validador externo, no expresable en JSON Schema puro → paso de validación semántica tras el schema) |
| `routing.capabilities[].strength` ∈ {primary, secondary} | Enum |
| `model.profile` debe existir en `models.yaml` | Referencial |
| `knowledge.sources[]` debe existir bajo `knowledge/` (o `LLM-Wiki/wiki/` mientras dure la migración, ver §11) | Referencial |
| `tools.allow[]` debe estar registrado en el catálogo MCP (§8) | Referencial |
| `permissions.policy_ref` debe existir en `permissions.yaml` | Referencial |
| `extends` (si existe) no debe formar ciclos | Semántica (resolución de herencia, §6) |
| Si `status: active` → deben existir los 12 archivos Markdown de §4 con contenido no vacío | Semántica |
| `evaluation.require_citations: true` → el Evaluador rechaza respuestas sin cita (ya documentado en 02-COMPONENTES, componente 9) | Runtime, no del schema |

### Dos niveles de validación (igual que Kubernetes admission control)

1. **Validación estructural** — JSON Schema puro, rápida, sin I/O. Corre en CI
   y en cada `magnus agent validate`.
2. **Validación referencial/semántica** — requiere el resto del sistema
   cargado (capacidades, modelos, herramientas, otros agentes para ciclos de
   herencia). Corre en `AgentRegistry.load_all()` y bloquea `activate()`.

Un manifiesto que falla (1) nunca llega a (2) — *fail fast*, igual que hoy
documenta `01-MAS-especificacion.md`, ahora con archivo real que lo hace
cumplible en CI.

---

## 6. Herencia (`extends`)

### Jerarquía propuesta

```
CorporateAgent                    # base de TODOS: constitución, principios base,
                                   # rúbrica mínima, política de citas
    └── KnowledgeWorker            # + memory.md base, + evaluation rigor mínimo 7,
                                   # + principio "evidencia antes que opinión"
        ├── Economist              # + capacidades finance/macroeconomics por defecto,
        │                          # + tools.allow base (fred, world_bank_api, imf_api)
        │       └── Ernesto Libras # override: personality.md propio, knowledge.sources propio
        ├── HealthAdvisor           # + principio "no diagnóstico clínico", + disclaimer obligatorio
        │       ├── Dr. Soma
        │       └── Serena
        ├── Strategist              # + workflows.md de planificación por fases
        │       └── Amanda
        └── TechnicalAdvisor        # + tools.allow (python, terminal en sandbox)
                └── Lexi
```

### Reglas de resolución (deterministas, auditable)

```python
class InheritanceResolver(Protocol):
    def resolve(self, agent_id: str) -> AgentSpec: ...   # AgentSpec ya "aplanado"
    def chain_of(self, agent_id: str) -> list[str]: ...  # para auditoría/explicabilidad
```

1. **Merge de YAML:** campos escalares → el hijo sobreescribe (`override`
   explícito, nunca implícito); listas (`tools.allow`, `routing.capabilities`)
   → **unión**, salvo que el hijo declare `override: true` en ese campo
   puntual; `deny` siempre gana sobre `allow` heredado (seguridad por encima
   de conveniencia — ninguna capa de herencia puede *re-habilitar* algo negado
   arriba).
2. **Merge de Markdown:** cada archivo (`identity.md`, `principles.md`, …) se
   **concatena** en orden ancestro→descendiente con un separador visible
   (`---\n## Heredado de <parent>\n`), nunca se sobreescribe silenciosamente —
   el prompt final debe ser auditable línea por línea.
3. **Ciclos:** `AgentRegistry.load_all()` detecta ciclos en `extends` y
   rechaza el arranque de esa rama completa (no del sistema entero).
4. **Versionado de la cadena:** cambiar `CorporateAgent` sube la versión
   efectiva de **todos** sus descendientes (propagación explícita, visible en
   el Dashboard como "N agentes afectados por este cambio") — evita que una
   base compartida cambie en silencio.

Esto es lo mismo que resuelve Docker con capas de imagen o Kubernetes con
`PodTemplate` + overrides de `Deployment`: una única base, muchas
especializaciones, sin duplicar texto.

---

## 7. Arquitectura para 500+ agentes

| Vector de escala | Mecanismo |
|---|---|
| **Descubrimiento** | Carga *lazy*: `AgentRegistry` indexa metadatos (`id`, `capabilities`, `status`) de todos, pero solo compila `AgentSpec` completo (Markdown + herencia resuelta) bajo demanda / LRU cache |
| **Matching** | Índice vectorial de **capacidades** (decenas/cientos, no de agentes) → el salto `capacidad→agentes` es una tabla hash, O(1) |
| **Caché** | Tres niveles: (a) `AgentSpec` compilado en memoria de proceso, (b) índice de capacidades en el vector store, (c) resultados de `CapabilityEngine.match()` por `(query_hash, capability_index_version)` con TTL corto |
| **Invalidación** | Cualquier cambio en `agents/*` o `capabilities/*` sube una `capability_index_version` monotónica; la caché de resultados se invalida por versión, no por TTL ciego |
| **Versionado** | Cada agente mantiene N versiones; el índice de capacidades solo indexa la versión `active` — versiones `deprecated` no compiten por tráfico nuevo pero siguen resolviéndose por `get(id, version)` para conversaciones en curso |
| **Particionado** | A partir de cientos de agentes: particionar el índice de capacidades por *dominio raíz* (finance, health, tech, …) — el primer salto de matching filtra por dominio antes de rankear dentro de él |
| **Multi-tenant** | Cada workspace tiene su propio `agents/` + `capabilities/` + índice; el Registry es *namespaced* por tenant desde el diseño, no como añadido posterior |
| **Hot-reload** | *File watcher* sobre `agents/` dispara `reload(agent_id)` de una sola definición, no un reinicio completo — necesario a esta escala para poder iterar un agente sin tumbar los otros 499 |

---

## 8. Integración MCP completa

Hoy `tools.allow/deny` en `agent.yaml` es una lista de nombres sueltos
(`kiwix`, `python`, `terminal`...) sin más estructura. Para que un agente
declare de verdad "qué MCP servers y qué herramientas necesita", el contrato
se enriquece:

```yaml
# agents/lexi/agent.yaml (fragmento propuesto)
tools:
  mcp_servers:
    - id: filesystem
      scope: read_only
    - id: python_sandbox
      scope: execute
    - id: browser
      scope: read_only          # sin envío de formularios, sin credenciales
  allow: [filesystem, python_sandbox, browser, search]
  deny:  [terminal, email, calendar]
```

- **`tools.mcp_servers`** declara servidores completos (con `scope`), no solo
  nombres de herramienta — un servidor MCP puede exponer varias tools, y el
  agente puede necesitar solo un subconjunto con un alcance restringido.
- **`ToolRegistry`** (ya diseñado en `02-COMPONENTES.md`, componente 8) pasa a
  validar en `activate()` que cada `mcp_servers[].id` esté en un catálogo
  central de servidores MCP conocidos por el runtime — el mismo catálogo que
  ya usa `.mcp.json` a nivel de proyecto, pero con **scope por agente**, no
  global.
- **Frontera de seguridad sin cambios de fondo:** `deny` sigue ganando sobre
  `allow` (regla ya en `permissions.yaml`); acciones con `external_side_effects`
  siguen requiriendo aprobación humana (constitución, principio 5).
- **Auditoría:** cada invocación de herramienta queda ligada a
  `(agent_id, agent_version, tool, scope, request_id)` — trazabilidad
  completa, no solo "se llamó a python".

---

## 9. Memoria desacoplada

Ya está bien diseñada en `02-COMPONENTES.md` (componente 5); aquí se fija como
**obligatoria y desacoplada del agente** — ningún agente implementa su propio
almacenamiento, todos hablan con el mismo `MemoryEngine` a través de `scope`:

| Tipo | Contenido | Vive en | Quién escribe |
|---|---|---|---|
| **Short Term** | Turno/sesión activa | Proceso/Redis | Runtime, por request |
| **Long Term** | Preferencias y hechos de usuario | Postgres | Runtime, tras consolidación |
| **Episodic** | Traza de qué pasó, decisiones tomadas | Event store | Runtime, automático |
| **Semantic** | Conocimiento "aprendido" propuesto | Vector store, namespace `memory/` | Agente **propone**, humano aprueba (P7) |
| **Project Memory** *(nuevo respecto a 02-COMPONENTES)* | Contexto de un proyecto/hilo multi-sesión del usuario (p. ej. "la migración de cartera que Ernesto lleva acompañando desde marzo") | Postgres, `scope=project_id` | Runtime, consolidación por proyecto en vez de por usuario global |

`memory.md` de cada agente declara **qué tipos usa** y con qué límite (p. ej.
Serena puede necesitar Project Memory para seguimiento de estado de ánimo a lo
largo de semanas; Ernesto probablemente no). El `MemoryEngine` es un puerto
único; el tipo "Project Memory" es una partición nueva del mismo backend de
Long/Episodic, no un sistema nuevo.

---

## 10. Flujo end-to-end (definitivo)

```
Usuario
   │
   ▼
Router                     — clasifica intención(es), decide modo (single/parallel/sequential)
   │
   ▼
Capability Engine           — resuelve intención → capacidades → agentes candidatos (con score y reason)
   │
   ▼
Agent Registry              — resuelve AgentSpec activo (herencia aplicada), valida permisos/herramientas
   │
   ▼
Knowledge Kernel             — namespaces del agente (desde AgentSpec, no hardcoded)
   │
   ▼
RAG                          — recupera evidencia con citas, filtrado por namespace+permisos
   │
   ▼
LLM Provider                 — resuelve model.profile → adaptador concreto, genera borrador
   │
   ▼
Evaluator                    — aplica rúbrica de evaluation.md; publish / retry / escalate
   │
   ▼
Router.merge()                — fusiona respuestas de N agentes si mode=parallel/sequential
   │
   ▼
Respuesta (+ trazas de auditoría: capacidades matcheadas, agente(s), versión, citas, coste)
```

Cada flecha es una llamada a un **puerto**, nunca a una implementación
concreta — es literalmente el mismo diagrama de `00-VISION-Y-ARQUITECTURA.md`
§6, con el salto "Router → Registro de agentes" corregido para pasar siempre
por el Capability Engine en medio.

---

## 11. Estructura definitiva del repositorio

```
MAGNUS/
├── agents/                      # ÚNICA fuente de verdad de agentes (MAS)
│   ├── _template/
│   ├── _base/                   # jerarquía de herencia: CorporateAgent, KnowledgeWorker,
│   │   ├── corporate_agent/      # Economist, HealthAdvisor, Strategist, TechnicalAdvisor
│   │   ├── knowledge_worker/
│   │   ├── economist/
│   │   ├── health_advisor/
│   │   ├── strategist/
│   │   └── technical_advisor/
│   ├── ernesto_libras/
│   ├── serena/
│   ├── amanda/
│   ├── lexi/
│   └── dr_soma/
├── capabilities/                 # NUEVO — catálogo de Capability (independiente de agentes)
│   ├── finance.capability.yaml
│   ├── macroeconomics.capability.yaml
│   ├── mental_health.capability.yaml
│   ├── project_management.capability.yaml
│   ├── medicine.capability.yaml
│   ├── programming.capability.yaml
│   └── brand_strategy.capability.yaml   # sin agente todavía, capacidad ya catalogada
├── schemas/                       # NUEVO
│   ├── agent.schema.json
│   └── capability.schema.json
├── knowledge/                     # LLM Wiki real (reemplaza a LLM-Wiki/wiki tras migración)
├── constitution/
├── orchestration/
│   ├── registry/                  # NUEVO módulo — antes vivía disperso/no existía
│   │   ├── agent_registry.py
│   │   ├── inheritance.py
│   │   └── capability_index.py
│   ├── capability_engine.py       # NUEVO — reemplaza engine.py::_route()
│   ├── router.py                  # se mantiene, ahora consume capability_engine
│   ├── planner.py                 # (pendiente, ya diseñado en 02-COMPONENTES)
│   ├── evaluator.py                # (pendiente, ya diseñado en 02-COMPONENTES)
│   └── memory/                    # NUEVO — implementación del Motor de Memoria
├── providers/                      # sin cambios de fondo
├── kernel/rag/                     # sin cambios de fondo
├── tools/                          # NUEVO — catálogo MCP + registry (hoy vive implícito en mcp_server/)
├── mcp_server/                     # transporte stdio/http, ahora delgado: solo llama a orchestration/
├── sdk/                            # NUEVO — Magnus Agent SDK (§13)
├── configs/
│   ├── models.yaml
│   ├── permissions.yaml
│   └── agents.yaml                 # NUEVO — registro de qué agentes están habilitados por entorno
├── tests/
│   ├── contract/                   # valida agent.schema.json contra cada agents/*
│   └── golden/                     # casos de enrutado esperado (regresión del Capability Engine)
└── docs/
    └── 04-MAGNUS-V2-ARQUITECTURA.md  # este documento
```

### Qué se elimina de forma explícita

- `orchestration/engine.py` completo (su lógica se reparte entre
  `capability_engine.py`, `router.py` y llamadas directas a `AgentRegistry`/`RAGPipeline`).
- Cualquier lista de agentes en código Python.
- `demo/` se conserva como *ejemplo de uso del SDK*, no como el único camino
  para probar el sistema (hoy es el único punto de entrada real).

---

## 12. Roadmap por fases

| Fase | Contenido | Prioridad | Depende de | Riesgo principal |
|---|---|---|---|---|
| **Fase 1 — Fundaciones de verdad única** | `agent.schema.json` + `capability.schema.json`; `AgentRegistry` real (carga + validación, sin índice todavía); eliminar `engine.py::default_config()` y las 4 definiciones hardcodeadas; migrar `ernesto_libras` al nuevo `agent.yaml` con `routing.capabilities` | **Crítica** | Ninguna (es la base) | Romper el MCP server en producción mientras se migra — mitigar con *feature flag* `MAGNUS_REGISTRY=v2` y ambas rutas coexistiendo brevemente |
| **Fase 2 — Capability Engine + agentes completos** | Catálogo `capabilities/*`; `CapabilityEngine` con índice de embeddings; completar los 12 archivos de Serena, Amanda, Lexi, Dr. Soma, y completar los que faltan en Ernesto | **Alta** | Fase 1 (necesita `agent.schema.json` para validar cada archivo) | Capacidades mal calibradas → *over-routing*; mitigar con `tests/golden/` (casos de enrutado esperado) antes de activar en producción |
| **Fase 3 — Herencia + escalabilidad** | Jerarquía `_base/` (CorporateAgent → … → agentes concretos); `InheritanceResolver`; caché de 3 niveles (§7); particionado del índice por dominio | **Media** | Fase 2 (necesita agentes reales para probar merge de herencia sin romper personalidad/tono) | Merge de Markdown ambiguo (¿qué pasa si dos ancestros contradicen `principles.md`?) — mitigar con orden estricto ancestro→descendiente y el hijo siempre gana en conflicto explícito |
| **Fase 4 — MCP enriquecido + memoria desacoplada** | `tools.mcp_servers` con `scope`; catálogo central de servidores MCP; `MemoryEngine` con las 5 particiones (§9), incluida Project Memory | **Media** | Fase 1 (Registry debe poder validar `mcp_servers[].id`) | Alcance (`scope`) mal aplicado permite una herramienta más peligrosa de lo declarado — mitigar con *contract tests* que verifiquen `scope` en cada invocación real, no solo en el manifiesto |
| **Fase 5 — Magnus Agent SDK** | CLI `magnus agent create/validate/activate/deprecate`; generador de esqueleto de los 12 archivos con prompts guiados; publicación como paquete instalable | **Media-Alta** (alto valor de adopción, pero no bloquea nada técnico) | Fases 1–2 (el SDK genera contra el schema y las capacidades ya existentes; sin ellas, genera esqueletos huecos) | Que el SDK cristalice una plantilla antes de que la Fase 2 valide que la plantilla es la correcta con agentes reales — mitigar construyendo el SDK **después** de tener Ernesto + Serena completos a mano, no antes |

**Orden recomendado:** 1 → 2 → 5 puede empezar en paralelo con 3/4 una vez
Fase 2 esté estable (el SDK no depende de herencia ni de MCP enriquecido para
dar valor inmediato). 3 y 4 son independientes entre sí y pueden ir en
paralelo.

---

## 13. Propuesta adicional: Magnus Agent SDK

Encaja como **Fase 5**, y es la pieza que faltaba en el análisis original: hoy
"crear un agente" es documentación (`cp -r agents/_template`) sin
herramienta. El SDK la convierte en un comando, igual que `kubectl create` o
`docker init` no son la arquitectura — son la forma de tocarla sin cometer
errores manuales.

### Comando ancla

```
magnus agent create serena
```

### Lo que debe hacer, en orden

1. **Modo interactivo guiado** (como `npm init`, no un formulario plano):
   pregunta capacidades (`primary`/`secondary` desde el catálogo existente en
   `capabilities/*`, con opción de crear una nueva capacidad si no existe),
   perfil de modelo (`model.profile` desde `models.yaml`), plantilla de
   herencia (`extends` desde `_base/*` si aplica).
2. **Genera el esqueleto completo** de los 12 archivos + `agent.yaml`, con
   `status: draft` y contenido *placeholder guiado* (no vacío — cada
   Markdown trae comentarios `<!-- completa: qué evita este agente -->` que
   el propio `magnus agent validate` exige eliminar antes de `activate`).
3. **Valida en el momento** contra `agent.schema.json` (estructural) —
   feedback inmediato, no al desplegar.
4. **No activa solo.** El SDK crea en `draft`; `activate` es un comando
   separado y explícito (`magnus agent activate serena`), que dispara la
   validación referencial completa (§5) — coherente con el principio de
   humano-en-el-bucle que ya rige el resto del sistema (P7 de la
   constitución).

### Subcomandos del SDK (superficie completa, no solo `create`)

```
magnus agent create <id>              # scaffolding guiado
magnus agent validate <id>            # ambos niveles de validación (§5)
magnus agent activate <id>            # draft → active
magnus agent deprecate <id>           # active → deprecated
magnus agent diff <id> <v1> <v2>      # qué cambió entre versiones (Markdown + yaml)
magnus agent explain <id> "<query>"   # por qué (no) matchea esta query — usa CapabilityEngine.explain()
magnus agent test <id>                # corre examples.md como casos de contrato contra el Evaluador
magnus capability create <id>         # scaffolding de una nueva Capability
magnus capability list                # catálogo con agentes que la implementan (o "sin agente" — huérfanas)
```

### Por qué eleva el proyecto (no es solo azúcar sintáctica)

- **Convierte la especificación en producto.** MAS deja de ser "un documento
  que hay que leer con cuidado" y pasa a ser "lo que el SDK genera
  correctamente por defecto" — reduce el riesgo que causó la desalineación
  original (documentación vs. runtime) porque la única forma cómoda de crear
  un agente pasa por la fuente de verdad.
- **`magnus agent test`** cierra un hueco que ni siquiera el diseño original
  cubría: hoy `examples.md` es prosa que nadie ejecuta. Convertirlo en casos
  de contrato contra el Evaluador es lo que hace que "construir completamente"
  un agente (objetivo 4) sea verificable, no solo "está escrito".
- **`magnus capability list`** con detección de capacidades huérfanas es la
  señal temprana de cuándo el catálogo de capacidades necesita un agente
  nuevo — información que hoy no existe en ningún lado.

---

## Resumen de la reconciliación

| Pregunta de la auditoría | Respuesta en V2 |
|---|---|
| ¿Quién define un agente? | Solo `agents/<id>/agent.yaml` + Markdown. Nada en código. |
| ¿Quién decide quién responde? | Capability Engine, por capacidades declaradas — no por nombre ni keywords. |
| ¿Quién valida que un agente esté bien formado? | `agent.schema.json`, en dos niveles, exigido antes de `activate()`. |
| ¿Cómo se evita duplicar personalidad/reglas entre agentes? | Herencia (`extends`) con merge determinista y auditable. |
| ¿Cómo escala a 500 agentes? | Índice de capacidades (no de agentes) + caché de 3 niveles + carga *lazy*. |
| ¿Cómo se declaran herramientas sin ambigüedad? | `tools.mcp_servers` con `scope`, validado contra un catálogo central. |
| ¿Dónde vive la memoria? | Fuera del agente, en `MemoryEngine` con 5 particiones, nunca en `knowledge/`. |
| ¿Cómo se crea un agente sin repetir el error de hoy? | Magnus Agent SDK — un comando, generado contra el schema vigente. |
