# Magnus Dynamic Group — Los 15 Componentes

> Para cada componente: **Objetivo · Responsabilidades · Interfaces · Flujo de datos · Tecnologías · Estructura de carpetas · Ejemplo de implementación · Riesgos técnicos · Escalabilidad.**
> Convención de puertos: las interfaces son *Ports* (Python `Protocol`/ABC); la infraestructura provee *Adapters*.

Índice: [1](#1-knowledge-kernel) · [2](#2-sistema-rag) · [3](#3-router-inteligente) · [4](#4-planificador-de-tareas) · [5](#5-motor-de-memoria) · [6](#6-registro-de-agentes) · [7](#7-adaptadores-de-proveedores-de-ia) · [8](#8-sistema-de-herramientas-mcp) · [9](#9-evaluador-de-respuestas) · [10](#10-auditoría-y-trazabilidad) · [11](#11-permisos-y-seguridad) · [12](#12-api-rest-y-cli) · [13](#13-dashboard-de-administración) · [14](#14-versionado-del-conocimiento) · [15](#15-aprendizaje-supervisado)

---

## 1. Knowledge Kernel

**Objetivo.** Ser la puerta única y gobernada al conocimiento (LLM Wiki). Convierte documentos crudos en conocimiento consultable, versionado y con procedencia.

**Responsabilidades.**
- Ingesta y normalización de fuentes (Markdown, PDF, HTML, ZIM/Kiwix, APIs).
- *Chunking* semántico + generación de embeddings (vía puerto `Embedder`).
- Indexación en el vector store por *namespace* (`economics/`, `finance/`…).
- Mantener metadatos de **procedencia** (fuente, fecha, hash, licencia, versión).
- Exponer el conocimiento como colección inmutable + versionada.

**Interfaces.**
```python
class KnowledgeKernel(Protocol):
    def ingest(self, source: SourceRef) -> IngestReport: ...
    def query(self, q: KnowledgeQuery) -> list[Chunk]: ...          # usado por RAG
    def namespaces(self) -> list[str]: ...
    def provenance(self, chunk_id: str) -> Provenance: ...
    def snapshot(self, version: str) -> KnowledgeSnapshot: ...      # ver Componente 14
```

**Flujo de datos.**
```
fuente → loader → normalizador → chunker → Embedder(puerto) → VectorStore(puerto)
                                        └→ metadatos+provenance → Postgres
```

**Tecnologías.** Qdrant/pgvector · `unstructured`/`docling` para parseo · bge-m3 o Voyage como embeddings · Postgres para metadatos · MinIO/S3 para binarios.

**Estructura.**
```
kernel/
├── ingestion/ (loaders/, chunkers/, normalizers/)
├── indexing/  (embedder_port.py, vectorstore_port.py)
├── provenance/
└── kernel.py
```

**Ejemplo.** Ver [`kernel/kernel.py` de referencia](#anexo-código). El agente nunca llama al Kernel directamente: pasa por el RAG con su filtro de namespace.

**Riesgos.**
- *Chunking* pobre → recuperación irrelevante. Mitigar con chunking jerárquico + evaluación de retrieval.
- Embeddings desalineados al cambiar de modelo → invalida el índice. Mitigar versionando el embedder y reindexando por lotes.
- Deriva de licencias/PII en ingesta. Mitigar con escáner de PII y registro de licencia por documento.

**Escalabilidad.** Ingesta por *workers* en cola; reindexación *blue/green* (índice nuevo en paralelo, *swap* atómico); *sharding* del vector store por namespace.

---

## 2. Sistema RAG

**Objetivo.** Recuperar el contexto *mínimo, relevante y citable* para cada consulta de agente. Es la única vía por la que un agente "sabe".

**Responsabilidades.**
- Reescritura/expansión de consulta (multi-query, HyDE opcional).
- Recuperación híbrida: densa (vector) + léxica (BM25).
- *Reranking* (cross-encoder) y compresión contextual.
- Filtrado por namespace del agente y por permisos.
- Ensamblado de contexto con **citas** trazables (chunk_id → provenance).

**Interfaces.**
```python
class Retriever(Protocol):
    def retrieve(self, query: str, *, namespaces: list[str],
                 top_k: int, filters: Filters) -> list[ScoredChunk]: ...

class RAGPipeline(Protocol):
    def build_context(self, req: RAGRequest) -> RAGContext:  # chunks + citas + tokens
        ...
```

**Flujo de datos.**
```
consulta → rewrite → [dense ∥ bm25] → union → rerank → compress
        → filtro(namespaces ∩ permisos) → RAGContext(chunks+citas)
```

**Tecnologías.** Qdrant (denso) + OpenSearch/`bm25s` (léxico) · reranker bge-reranker/cohere · LangChain/LlamaIndex *opcionales* (preferible orquestación propia y delgada).

**Estructura.**
```
kernel/rag/
├── rewrite.py
├── retrievers/ (dense.py, lexical.py, hybrid.py)
├── rerank.py
├── compress.py
└── pipeline.py
```

**Ejemplo.**
```python
ctx = rag.build_context(RAGRequest(
    query="¿Perspectiva de inflación de la eurozona 2026?",
    namespaces=["economics/", "imf/", "federal_reserve/"],
    top_k=8, require_citations=True))
# ctx.chunks[i].citation → Provenance(source="IMF WEO 2026", date=..., hash=...)
```

**Riesgos.** Recuperación de baja precisión; *prompt injection* vía documentos; explosión de tokens. Mitigar con reranking, saneo de contenido recuperado (tratar el documento como **dato, no instrucción**), y presupuesto de tokens.

**Escalabilidad.** Cache de resultados por (consulta⊕namespace⊕versión de índice); recuperación paralela dense/léxica; *precomputo* de multi-query.

---

## 3. Router Inteligente

**Objetivo.** El "cerebro" de Magnus: convertir una petición ambigua en un plan de agentes. Detecta intención(es) y decide *quién* responde, *solo* o *en colaboración*.

**Responsabilidades.**
- Clasificar intención(es) y dominio(s) de la petición.
- Seleccionar 1..N agentes vía índice semántico del Registro.
- Decidir modo: *single*, *paralelo* (fan-out) o *secuencial* (pipeline).
- Entregar al Planner cuando la tarea es compleja; **fusionar** respuestas al final.

**Interfaces.**
```python
class Router(Protocol):
    def route(self, request: UserRequest) -> RoutePlan: ...
    def merge(self, request: UserRequest, answers: list[AgentAnswer]) -> FinalAnswer: ...
```

**Flujo de datos.**
```
UserRequest → clasificador(intents) → match agentes(embedding routing.*)
           → RoutePlan{agents, mode, deps} → (Planner si complejo)
                                            → respuestas → merge → FinalAnswer
```

**Tecnologías.** LLM ligero (perfil `routing_fast`) para clasificación + embeddings de `identity/skills` para *matching*; reglas de negocio en YAML para desempates.

**Estructura.**
```
orchestration/router.py
orchestration/routing/ (classifier.py, agent_index.py, merger.py)
```

**Ejemplo.** *"Cambiar de trabajo por estrés + invertir dinero"* → `RoutePlan(agents=[serena, ernesto, amanda], mode=parallel)` → merge en informe único. (Ver [`orchestration/router.py`](#anexo-código).)

**Riesgos.** Enrutado incorrecto; *over-routing* (invoca demasiados agentes → coste/latencia). Mitigar con umbral de confianza, límite de agentes por petición y *trazas* de decisión auditable.

**Escalabilidad.** El índice de agentes es un vector index → O(log n) para cientos de agentes; el Router es *stateless* → réplicas horizontales.

---

## 4. Planificador de Tareas

**Objetivo.** Descomponer objetivos complejos en un DAG de sub-tareas con dependencias, recursos y criterios de éxito.

**Responsabilidades.**
- Planificación jerárquica (goal → tasks → steps).
- Definir dependencias, paralelismo y *presupuesto* (tokens/tiempo/coste).
- Reasignar (replanning) ante fallos o nueva evidencia.
- Delegación a agentes/herramientas (a través de `Delegation`).

**Interfaces.**
```python
class Planner(Protocol):
    def plan(self, goal: Goal, ctx: PlanningContext) -> TaskGraph: ...
    def replan(self, graph: TaskGraph, event: DomainEvent) -> TaskGraph: ...

class Delegation(Protocol):
    def dispatch(self, task: Task) -> TaskResult: ...
```

**Flujo de datos.**
```
Goal → plan → TaskGraph(DAG) → scheduler → Delegation → agentes/tools
              ▲                                   │
              └──────── replan(evento) ◄──────────┘
```

**Tecnologías.** Grafo de tareas propio (networkx en memoria + persistencia en Postgres) · cola (Redis Streams/NATS) · patrón *saga* para compensaciones.

**Estructura.** `orchestration/planner.py`, `orchestration/delegation.py`, `orchestration/scheduler.py`.

**Ejemplo.**
```python
graph = planner.plan(Goal("Informe económico Q3 con escenarios"),
                     ctx=PlanningContext(budget=Budget(tokens=200_000)))
for task in scheduler.ready(graph):
    delegation.dispatch(task)   # emite TaskCompleted/TaskFailed al EventBus
```

**Riesgos.** Bucles/planes infinitos, explosión combinatoria, coste descontrolado. Mitigar con límites de profundidad, presupuestos duros (task budget) y *timeouts*.

**Escalabilidad.** *Scheduler* distribuido; tareas idempotentes con `task_id`; ejecución paralela de ramas independientes del DAG.

---

## 5. Motor de Memoria (corto, largo, episódica, semántica)

**Objetivo.** Dar continuidad y aprendizaje a las interacciones sin violar P1/P3: la memoria guarda *interacciones y hechos aprendidos*, no reemplaza a la LLM Wiki.

**Tipos.**
| Tipo | Contenido | Backend | TTL |
|------|-----------|---------|-----|
| **Corto plazo** | Turno/sesión actual, scratchpad | Redis | minutos–horas |
| **Largo plazo** | Preferencias, hechos de usuario, historial | Postgres | persistente |
| **Episódica** | "Qué pasó" (traza de tareas y decisiones) | Postgres + event store | persistente |
| **Semántica** | Conocimiento *aprendido* (propuesto) | Vector store (namespace `memory/`) | tras aprobación (P7) |

**Responsabilidades.** Escribir/leer por tipo; consolidar (short→long); *compaction* de contexto largo; enrutar la **memoria semántica al flujo de aprendizaje supervisado** (nunca escritura directa a `knowledge/`).

**Interfaces.**
```python
class MemoryEngine(Protocol):
    def remember(self, scope: MemoryScope, item: MemoryItem) -> None: ...
    def recall(self, scope: MemoryScope, query: str, k: int) -> list[MemoryItem]: ...
    def consolidate(self, session_id: str) -> ConsolidationReport: ...
    def propose_semantic(self, item: SemanticFact) -> ProposalId: ...   # → Componente 15
```

**Flujo de datos.**
```
turno → short(Redis) ──consolidate──► long(Postgres)
tarea → episodic(event store)
insight → propose_semantic ──► Aprendizaje supervisado ──(humano)──► knowledge/
```

**Tecnologías.** Redis (corto) · Postgres (largo/episódica) · Qdrant (semántica) · *summarizer* con perfil LLM barato.

**Estructura.** `memory/{short_term,long_term,episodic,semantic}/`, `orchestration/memory_manager.py`.

**Ejemplo.**
```python
mem.remember(MemoryScope(user="u1", type="long"), MemoryItem(text="Prefiere análisis con gráficos"))
insight = SemanticFact(text="El usuario invierte con perfil conservador", evidence=[...])
mem.propose_semantic(insight)   # NO escribe knowledge/: crea una propuesta
```

**Riesgos.** Contaminación de memoria (hechos falsos), fuga de PII, *drift*. Mitigar con verificación contra RAG antes de proponer, cifrado/anonimizado y `semantic_write: proposal_only`.

**Escalabilidad.** Partición por usuario/tenant; TTL agresivo en corto plazo; consolidación asíncrona por lotes.

---

## 6. Registro de Agentes

**Objetivo.** Fuente de verdad de *qué agentes existen* y sus capacidades. Carga, valida y versiona definiciones MAS.

**Responsabilidades.** Descubrir `agents/*`; validar `agent.yaml` contra `mas.schema.json`; construir el **índice semántico de agentes** para el Router; gestionar ciclo de vida (draft/active/deprecated) y versiones.

**Interfaces.**
```python
class AgentRegistry(Protocol):
    def load_all(self) -> None: ...
    def get(self, agent_id: str, version: str | None = None) -> AgentSpec: ...
    def search(self, intent: str, k: int) -> list[AgentMatch]: ...   # para Router
    def validate(self, spec_dir: Path) -> ValidationResult: ...
```

**Flujo de datos.**
```
agents/*/agent.yaml → validate(MAS schema) → AgentSpec → index(embed routing+skills)
                                                       → cache en memoria + Postgres
```

**Tecnologías.** Pydantic (validación) · Qdrant (índice de agentes) · Postgres (registro/versiones) · *hot-reload* con watcher de ficheros.

**Estructura.** `orchestration/registry/` (`registry.py`, `mas_schema.py`, `agent_index.py`).

**Ejemplo.**
```python
registry.load_all()
matches = registry.search("proyección económica", k=3)  # → [ernesto_libras (0.91), ...]
spec = registry.get("ernesto_libras")                    # AgentSpec completo
```

**Riesgos.** Definiciones inválidas rompiendo el arranque; colisión de dominios entre agentes. Mitigar con validación *fail-fast*, *health checks* por agente y desempate por `priority`.

**Escalabilidad.** Carga *lazy* de definiciones; índice de agentes en vector store → cientos/miles de agentes sin degradar el Router.

---

## 7. Adaptadores de Proveedores de IA

**Objetivo.** Hacer el modelo **intercambiable**. Un único puerto `LLMProvider`; adaptadores para OpenAI, Anthropic, Google, Mistral, Ollama, OpenRouter, etc.

**Responsabilidades.** Traducir la petición canónica de Magnus (`LLMRequest`) al SDK de cada proveedor y normalizar la respuesta; gestionar *streaming*, *tool-calling*, reintentos, *rate limits*, coste y *fallbacks*; resolver `model.profile` (MAS) → modelo concreto vía `models.yaml`.

**Interfaces.**
```python
class LLMProvider(Protocol):
    name: str
    def complete(self, req: LLMRequest) -> LLMResponse: ...
    def stream(self, req: LLMRequest) -> Iterator[LLMChunk]: ...
    def supports(self, cap: Capability) -> bool: ...   # tools, thinking, vision, effort…

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[Vector]: ...
```

**Flujo de datos.**
```
LLMRequest(canónico) → resolver perfil → Adapter concreto → SDK proveedor
                    ← LLMResponse(normalizado: texto, tool_calls, usage, citations)
```

**Tecnologías.** SDKs oficiales por proveedor. Ejemplo Anthropic: `anthropic` (Python), modelo por defecto `claude-opus-4-8`, *adaptive thinking* + `effort` para el perfil `reasoning_high`. Ollama para local. OpenRouter como *meta-proveedor*.

**`configs/models.yaml` (perfiles → modelos).**
```yaml
profiles:
  reasoning_high:
    primary:  { provider: anthropic, model: "claude-opus-4-8", effort: high, thinking: adaptive }
    fallback: { provider: openai,    model: "gpt-..." }
  reasoning_std:
    primary:  { provider: anthropic, model: "claude-sonnet-5" }
  routing_fast:
    primary:  { provider: anthropic, model: "claude-haiku-4-5" }
    fallback: { provider: ollama,    model: "llama3.1:8b" }
  local_private:
    primary:  { provider: ollama,    model: "qwen2.5:14b" }
providers:
  anthropic: { api_key_env: ANTHROPIC_API_KEY }
  openai:    { api_key_env: OPENAI_API_KEY }
  ollama:    { base_url: "http://localhost:11434" }
  openrouter:{ api_key_env: OPENROUTER_API_KEY }
```

**Estructura.** `providers/` (`base.py`, `anthropic_provider.py`, `openai_provider.py`, `google_provider.py`, `mistral_provider.py`, `ollama_provider.py`, `openrouter_provider.py`, `registry.py`).

**Ejemplo.** Ver [`providers/base.py` y `providers/anthropic_provider.py`](#anexo-código).

**Riesgos.** Divergencia de capacidades entre proveedores (thinking, tools, tamaño de contexto); *lock-in* accidental. Mitigar con capacidades declaradas (`supports()`), *contract tests* comunes a todos los adaptadores y una petición canónica mínima común.

**Escalabilidad.** *Pool* de clientes; *circuit breaker* por proveedor; enrutado por coste/latencia; *fallback* automático (perfil `fallback`).

---

## 8. Sistema de Herramientas (MCP)

**Objetivo.** Dar a los agentes capacidades del mundo real (buscar, ejecutar, leer archivos, llamar APIs) de forma segura y estandarizada mediante **Model Context Protocol**.

**Responsabilidades.** Registrar servidores/herramientas MCP; exponer catálogo por agente (según `tools.allow/deny`); mediar la invocación con permisos y auditoría; normalizar resultados como `ToolResult`.

**Interfaces.**
```python
class ToolRegistry(Protocol):
    def catalog(self, agent: AgentSpec) -> list[ToolSpec]: ...
    def invoke(self, call: ToolCall, ctx: SecurityContext) -> ToolResult: ...
```

**Flujo de datos.**
```
LLM tool_call → ToolRegistry.invoke → check(Permisos) → MCP server → ToolResult
                                     └→ Auditoría(registro de la invocación)
```

**Tecnologías.** Servidores MCP (kiwix, filesystem, terminal, browser, search, calendar, email, `magnus_capital`); patrón de *human-in-the-loop* para acciones irreversibles.

**Estructura.** `tools/` (uno por herramienta) + `tools/registry.py` + `tools/mcp_client.py`.

**Ejemplo.**
```python
result = tools.invoke(ToolCall(name="world_bank_api", args={"indicator":"NY.GDP.MKTP.CD","country":"ES"}),
                      ctx=security_ctx_for(ernesto))
```

**Riesgos.** Ejecución peligrosa (terminal, email), *prompt injection* que dispara herramientas, exfiltración. Mitigar con lista blanca por agente, aprobación humana para acciones con efectos externos, *sandbox* de terminal/filesystem y saneo de argumentos.

**Escalabilidad.** Herramientas como microservicios MCP independientes; *rate limiting* por herramienta; caché de resultados idempotentes.

---

## 9. Evaluador de Respuestas

**Objetivo.** Garantizar calidad: ninguna respuesta se publica sin pasar la rúbrica del agente (evidencia, corrección, citas, confianza).

**Responsabilidades.** Aplicar la rúbrica de `evaluation.md`; verificar citas contra el RAG (¿la afirmación está respaldada por el chunk citado?); detectar alucinaciones y sesgo; puntuar y decidir *publicar / reintentar / escalar a humano*.

**Interfaces.**
```python
class Evaluator(Protocol):
    def evaluate(self, answer: AgentAnswer, ctx: RAGContext,
                 rubric: Rubric) -> Evaluation: ...  # score, veredicto, hallazgos
```

**Flujo de datos.**
```
AgentAnswer + RAGContext + Rubric → juez(LLM + reglas) → Evaluation
   veredicto: publish | retry | escalate  (+ score, citas verificadas, gaps)
```

**Tecnologías.** LLM-as-judge (perfil `reasoning_std`) + verificación programática de citas (NLI/entailment) + reglas duras (require_citations, rigor).

**Estructura.** `orchestration/evaluator.py`, `orchestration/eval/` (`rubric.py`, `citation_check.py`, `judge.py`).

**Ejemplo.**
```python
ev = evaluator.evaluate(answer, ctx, rubric=registry.rubric("evidence_strict"))
if ev.verdict == "retry": planner.replan(...)
elif ev.verdict == "escalate": escalate_to_human(answer, ev)
```

**Riesgos.** Juez sesgado/inconsistente, coste añadido, *self-preference*. Mitigar con juez independiente del generador, rúbricas explícitas, verificación de citas *programática* (no solo LLM) y *evals* offline.

**Escalabilidad.** Evaluación asíncrona; muestreo (evaluar 100% de respuestas críticas, N% del resto); cache de veredictos por hash de respuesta.

---

## 10. Sistema de Auditoría y Trazabilidad

**Objetivo.** Registrar *todo* lo que ocurre de forma inmutable y consultable: cada decisión de Router, cada recuperación RAG, cada llamada a proveedor/herramienta, cada evaluación.

**Responsabilidades.** *Event sourcing* de eventos de dominio; *trace* end-to-end (OpenTelemetry) por `request_id`; registro de coste/tokens; retención y export para cumplimiento.

**Interfaces.**
```python
class AuditLog(Protocol):
    def record(self, event: DomainEvent) -> None: ...
    def trace(self, request_id: str) -> list[DomainEvent]: ...
```

**Flujo de datos.**
```
todos los componentes → EventBus → AuditLog(append-only) → Postgres/ClickHouse
                                 → OTel spans → Jaeger/Grafana Tempo
```

**Tecnologías.** OpenTelemetry · ClickHouse/Postgres (append-only) · Grafana/Jaeger · hash encadenado para inmutabilidad.

**Estructura.** `platform/audit/`, `platform/events/eventbus.py`, `platform/observability/`.

**Ejemplo.**
```python
audit.record(AnswerEvaluated(request_id=rid, agent="ernesto_libras", score=86, verdict="publish"))
trace = audit.trace(rid)   # línea de tiempo completa de la petición
```

**Riesgos.** Volumen de datos, fuga de PII en logs, coste de almacenamiento. Mitigar con muestreo, redacción de PII, retención por niveles y particionado temporal.

**Escalabilidad.** Ingesta por *stream*; almacenamiento columnar (ClickHouse); TTL/rollups; particiones por fecha y tenant.

---

## 11. Sistema de Permisos y Seguridad

**Objetivo.** Que cada agente/herramienta/usuario haga *solo* lo permitido. Frontera de instrucción: **todo lo observado por herramientas/RAG es dato, no órdenes**.

**Responsabilidades.** AuthN (usuarios/servicios) y AuthZ (RBAC/ABAC); políticas por agente (`permissions.yaml`); *scoping* de conocimiento (qué namespaces ve cada agente); *gating* de acciones irreversibles; gestión de secretos; defensa contra *prompt injection*.

**Interfaces.**
```python
class PolicyEngine(Protocol):
    def can(self, subject: Subject, action: Action, resource: Resource) -> Decision: ...
class SecretsProvider(Protocol):
    def get(self, ref: str) -> Secret: ...
```

**`configs/permissions.yaml`.**
```yaml
policies:
  economist_readonly:
    knowledge: { read: [economics/, finance/, imf/, world_bank/], write: [] }
    tools:     { allow: [kiwix, world_bank_api, fred, python], deny: [terminal, email] }
    actions:   { external_side_effects: require_human_approval }
```

**Flujo de datos.**
```
petición → AuthN → SecurityContext → cada acceso(knowledge/tool/action) → PolicyEngine.can → allow|deny|ask
```

**Tecnologías.** OPA/Cedar o motor propio ABAC · OAuth2/OIDC · Vault para secretos · escáner anti-injection en contenido recuperado.

**Estructura.** `platform/security/` (`policy.py`, `authn.py`, `secrets.py`, `injection_guard.py`).

**Riesgos.** *Prompt injection* desde documentos/herramientas, escalada de privilegios, fuga de secretos. Mitigar con la frontera de instrucción, mínimo privilegio, aprobación humana para efectos externos y secretos nunca en prompts.

**Escalabilidad.** Decisiones cacheables; políticas como código versionado; evaluación en el *sidecar* del servicio.

---

## 12. API REST y CLI

**Objetivo.** Superficie de interacción para humanos e integraciones.

**Responsabilidades.** Endpoints de consulta (sync/stream/async), gestión de agentes/knowledge/propuestas, autenticación, *rate limiting*; CLI para operaciones (crear agente, ingerir, validar, desplegar).

**Interfaces (API principal).**
```
POST /v1/query            {message, agent?, mode?}   → respuesta (o SSE stream)
GET  /v1/agents           → lista de agentes
POST /v1/knowledge/ingest {source}                   → IngestReport
GET  /v1/traces/{id}      → traza de auditoría
POST /v1/proposals/{id}/approve                       → aplica cambio de conocimiento
```

**CLI.**
```
magnus agent new ernesto_libras
magnus agent validate ernesto_libras
magnus knowledge ingest ./docs/economics --namespace economics/
magnus ask "perspectiva de inflación eurozona 2026" --agent ernesto_libras
magnus proposals list --status pending
```

**Flujo de datos.**
```
cliente → API(FastAPI) → casos de uso(orchestration) → respuesta/stream
CLI → mismos casos de uso vía cliente local o HTTP
```

**Tecnologías.** FastAPI + Pydantic · SSE/WebSocket para streaming · Typer/Click para CLI · OpenAPI autogenerado.

**Estructura.** `api/` (`server.py`, `routers/`, `schemas/`), `cli/` (`main.py`, `commands/`).

**Riesgos.** Superficie de ataque, *abuse*/coste, exposición de datos. Mitigar con authz por endpoint, *rate limiting*, cuotas por tenant y validación estricta de entrada.

**Escalabilidad.** API *stateless* tras balanceador; tareas largas → cola + webhook/polling; versionado de API (`/v1`).

---

## 13. Dashboard de Administración

**Objetivo.** Ventana operativa: observar, gobernar y **aprobar** (cierra el ciclo humano-en-el-bucle).

**Responsabilidades.** Visualizar agentes y su salud; explorar `knowledge/` y versiones; **bandeja de propuestas** (aprobar/rechazar cambios de conocimiento — Componente 15); ver trazas/costes; editar permisos y `models.yaml`; métricas de calidad (scores del Evaluador).

**Interfaces.** Consume la API REST (`/v1/*`). Vistas clave: *Agents*, *Knowledge*, *Proposals*, *Traces*, *Costs*, *Quality*, *Settings*.

**Flujo de datos.**
```
Dashboard(SPA) ↔ API REST ↔ dominio
Proposals view → POST /proposals/{id}/approve → Versionado del conocimiento
```

**Tecnologías.** React/Next.js + TypeScript · gráficos con la guía de dataviz · WebSocket para trazas en vivo · autenticación OIDC.

**Estructura.** `dashboard/` (frontend) + endpoints en `api/routers/admin.py`.

**Riesgos.** Acceso administrativo sensible; aprobación negligente de propuestas. Mitigar con RBAC estricto, doble confirmación en acciones destructivas y *diff* claro en cada propuesta.

**Escalabilidad.** SPA servida por CDN; backend ya escalado (API); paginación/streaming de trazas.

---

## 14. Versionado del Conocimiento

**Objetivo.** Tratar `knowledge/` como código: versionado, reproducible y auditable. Toda respuesta puede atarse a *qué versión* del conocimiento la produjo.

**Responsabilidades.** Versionar documentos y el índice vectorial; *snapshots* inmutables; *diffs* entre versiones; *rollback*; ligar cada `RAGContext`/respuesta a un `knowledge_version`.

**Interfaces.**
```python
class KnowledgeVersioning(Protocol):
    def commit(self, change: KnowledgeChange, author: str) -> Version: ...
    def diff(self, a: Version, b: Version) -> KnowledgeDiff: ...
    def rollback(self, to: Version) -> None: ...
    def resolve(self, version: str) -> KnowledgeSnapshot: ...
```

**Flujo de datos.**
```
propuesta aprobada → commit(knowledge/) → reindex(blue/green) → nueva Version
respuesta ← etiqueta knowledge_version (trazabilidad reproducible)
```

**Tecnologías.** Git (texto/Markdown) + DVC/LakeFS (binarios y snapshots de índice) · versionado de colecciones en el vector store · hashes de contenido.

**Estructura.** `kernel/versioning/`, integración con `knowledge/` (repo) y `platform/audit/`.

**Ejemplo.**
```python
v = versioning.commit(KnowledgeChange(add=["economics/inflation_2026.md"]), author="analyst_ana")
# el índice se reconstruye blue/green; respuestas nuevas citan knowledge_version=v.id
```

**Riesgos.** Índice y documentos desincronizados; *rollback* costoso; binarios grandes. Mitigar con reindexado atómico *blue/green*, DVC para binarios y verificación de consistencia índice↔versión.

**Escalabilidad.** Snapshots incrementales; reindexado por namespace afectado; retención por políticas.

---

## 15. Aprendizaje Supervisado (los agentes proponen; un humano aprueba)

**Objetivo.** Mejorar el conocimiento **sin** dejar que los agentes escriban solos. Encarna P7: los agentes **proponen** cambios; un humano **aprueba**.

**Responsabilidades.** Recoger propuestas (de memoria semántica, del Evaluador, de *gaps* detectados en RAG); estructurarlas con evidencia y *diff*; gestionar la cola de revisión; al aprobar, disparar el Versionado del conocimiento; *feedback loop* de calidad.

**Interfaces.**
```python
class ProposalService(Protocol):
    def submit(self, p: KnowledgeProposal) -> ProposalId: ...
    def review(self, id: ProposalId) -> ProposalDetail: ...   # incluye diff + evidencia
    def approve(self, id: ProposalId, reviewer: str) -> Version: ...
    def reject(self, id: ProposalId, reviewer: str, reason: str) -> None: ...
```

**Flujo de datos.**
```
agente/memoria/evaluador → submit(Proposal{diff, evidence, confidence})
   → cola de revisión (Dashboard) → humano aprueba/rechaza
        aprueba → KnowledgeVersioning.commit → reindex → knowledge/ mejora
        rechaza → feedback → ajuste de agente/rúbrica
```

**Tecnologías.** Cola de propuestas en Postgres · *diff* visual en Dashboard · reglas de *auto-triage* (propuestas de baja confianza se descartan; alta confianza + evidencia sólida se priorizan).

**Estructura.** `orchestration/learning/` (`proposal_service.py`, `triage.py`), vistas en Dashboard, integración con Componentes 5, 9 y 14.

**Ejemplo.**
```python
pid = proposals.submit(KnowledgeProposal(
    namespace="economics/", diff=Diff(add="Nueva serie de inflación IMF 2026"),
    evidence=[Provenance(source="IMF WEO", ...)], confidence=0.83, agent="ernesto_libras"))
# Aparece en el Dashboard → un humano revisa el diff+evidencia → approve()
```

**Riesgos.** Cuello de botella humano; propuestas de baja calidad; *feedback* que sesga agentes. Mitigar con *auto-triage* por confianza/evidencia, revisión por lotes, y métricas de tasa de aprobación por agente.

**Escalabilidad.** *Triage* automático filtra ruido; revisión por muestreo en dominios maduros; múltiples revisores por namespace.

---

## Anexo: Código

Esqueletos de referencia (ejecutables como guía de contrato, no producción):
- [`providers/base.py`](../providers/base.py) — puerto `LLMProvider` canónico.
- [`providers/anthropic_provider.py`](../providers/anthropic_provider.py) — adaptador Anthropic (modelo por defecto `claude-opus-4-8`).
- [`orchestration/router.py`](../orchestration/router.py) — Router multiagente (detección de intención + fusión).
- [`kernel/rag/pipeline.py`](../kernel/rag/pipeline.py) — pipeline RAG con citas.
