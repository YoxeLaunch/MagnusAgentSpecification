# Magnus Dynamic Group

**Sistema operativo de agentes inteligentes, independiente del proveedor de IA.**

> Los agentes **no** almacenan conocimiento: saben *dónde buscarlo*. El
> conocimiento vive en una base documental versionada (LLM Wiki) y se consulta
> por RAG. Todos los agentes siguen un estándar común: **MAS** (Magnus Agent
> Specification). El modelo de IA es intercambiable a través del puerto
> `LLMProvider`: **hoy hay adaptadores para Anthropic y Ollama**; OpenAI,
> Google, Mistral y OpenRouter son diseño pendiente (paso 6 del
> [ROADMAP](ROADMAP.md)), no capacidades disponibles.

## Documentación de diseño

| Documento | Contenido |
|-----------|-----------|
| [`docs/00-VISION-Y-ARQUITECTURA.md`](docs/00-VISION-Y-ARQUITECTURA.md) | Tesis, principios, Clean/DDD/EDA/Hexagonal, capas, flujo multiagente, escalabilidad. |
| [`docs/01-MAS-especificacion.md`](docs/01-MAS-especificacion.md) | El estándar MAS: estructura de un agente, `agent.yaml`, validación, herencia. |
| [`docs/02-COMPONENTES.md`](docs/02-COMPONENTES.md) | Los 15 componentes con objetivo, interfaces, flujo, tecnologías, riesgos y escalabilidad. |
| [`docs/04-MAGNUS-V2-ARQUITECTURA.md`](docs/04-MAGNUS-V2-ARQUITECTURA.md) | **Normativo.** Reconciliación runtime↔docs: Agent Registry, Capability Engine, `agent.schema.json`, herencia, escalabilidad a 500+ agentes, Magnus Agent SDK y roadmap por fases. |

## Estructura del repositorio

Lo que existe hoy en el repositorio:

```
MAGNUS/
├── LLM-Wiki/wiki/   # base documental versionada (fuente de verdad del conocimiento)
├── agents/          # Un directorio por agente conforme a MAS (+ _base, _template)
├── capabilities/    # Catálogo de Capability (taxonomía de enrutado)
├── constitution/    # Constitución, ética, evidencia, citación
├── orchestration/   # Motor, enrutado, evaluación, permisos, privacidad, auditoría
├── providers/       # Adaptadores de proveedores de IA (puerto LLMProvider)
├── kernel/rag/      # Ingesta, retriever léxico, vectorial local y pipeline
├── mcp_server/      # Servidor MCP (stdio y HTTP) + controles de acceso
├── evaluation/      # Banco de recuperación y goldens
├── configs/         # models.yaml, permissions.yaml, privacy.yaml, guardrails.yaml
├── tests/           # Suite de verificación (pytest)
└── docs/            # Diseño (arriba)
```

`orchestration/memory/` tiene esqueletos **no conectados** al motor, y
`docs/` describe además componentes (planner, bus de eventos) que son diseño,
no código. La [tabla de estado del ROADMAP](ROADMAP.md) dice de cada uno si
está implementado y si está conectado al runtime.

## Esqueletos de referencia incluidos

- [`providers/base.py`](providers/base.py) — puerto canónico `LLMProvider` + `Embedder`.
- [`providers/anthropic_provider.py`](providers/anthropic_provider.py) — adaptador Anthropic (`claude-opus-4-8` por defecto).
- [`providers/registry.py`](providers/registry.py) — resolución de perfiles + fallback.
- [`orchestration/router.py`](orchestration/router.py) — Router multiagente (intención → agentes → fusión).
- [`kernel/rag/pipeline.py`](kernel/rag/pipeline.py) — pipeline RAG híbrido con citas.
- [`agents/ernesto_libras/`](agents/ernesto_libras/) — agente de ejemplo conforme a MAS.

## Cómo recupera (RAG)

Recuperación **híbrida léxica + vectorial local**, sobre los mismos chunks:

| Retriever | Implementación | Qué aporta |
|---|---|---|
| Léxico | [`kernel/rag/file_store.py`](kernel/rag/file_store.py) | TF por solape de tokens normalizados |
| Vectorial local | [`kernel/rag/embedder.py`](kernel/rag/embedder.py) + [`vector_store.py`](kernel/rag/vector_store.py) | **random indexing con pesos TF-IDF** sobre unigramas, bigramas y prefijos, con coseno normalizado |

Los dos rankings se fusionan con Reciprocal Rank Fusion para el **orden**,
mientras el **umbral** (`min_score` de cada agente) se aplica sobre el score
original de cada retriever, que es la escala en la que está calibrado.

**Qué NO es:** el retriever vectorial **no usa embeddings neuronales**. No hay
`bge-m3`, ni sentence-transformers, ni torch, ni Qdrant en este repositorio. No
conoce sinónimos que no compartan forma: "paro" y "desempleo" siguen sin
parecerse. Lo que aporta frente al léxico es IDF, bigramas, prefijos (importa
mucho en español) y coseno normalizado. Un embedder neuronal se enchufa en el
mismo puerto `Embedder` sin tocar la orquestación — es trabajo del paso 6.

Medido sobre la wiki real (`python -m evaluation.bench_retrieval`, recall@8):
léxico solo 89.5%, vectorial solo 73.7%, **híbrido 94.7%**.

## Cómo enruta (Capability Matching)

Enrutado **léxico + vectorial local basado en random indexing/TF-IDF** —
igual tecnología que el RAG de arriba, **no embeddings neuronales**. El
`CapabilityEngine` (qué agente atiende una consulta) usa por defecto
`HybridCapabilityMatcher` ([`orchestration/capability/matcher.py`](orchestration/capability/matcher.py)),
que combina dos matchers intercambiables sobre el mismo protocolo
`CapabilityMatcher`:

| Matcher | Qué hace |
|---|---|
| `LexicalCapabilityMatcher` | Solape de tokens ponderado por IDF sobre `description`/`routing_examples`/`synonyms` de cada capacidad, propagado por la taxonomía (ancestros y relacionados). |
| `EmbeddingCapabilityMatcher` | Coseno con `HashingEmbedder` (el mismo del RAG) + un canal de **sinónimo exacto**: si una palabra de `synonyms` aparece literalmente en la consulta, la capacidad se marca con confianza máxima. |

**Medido antes de combinarlos, no asumido:** el coseno puro es más débil
que el léxico para el corpus de capacidades (textos de 20-40 palabras, muy
pocos como para que el IDF discrimine bien) y en varias consultas reales
apuntaba a la capacidad *incorrecta* con más confianza que a la correcta
(p. ej. "quiero mejorar mi alimentación diaria" → el coseno solo prefería
`project_management` sobre la `nutrition` correcta). Dejar que decidiera
solo habría **aumentado** las rutas incorrectas. Por eso el combinador es
conservador:

- El canal léxico manda: si ya identifica una capacidad, se respeta tal cual.
- El **sinónimo exacto** es la única vía por la que el canal vectorial puede
  incluir una capacidad por sí solo — es determinista y curado a mano, no
  una similitud difusa.
- El coseno puro solo puede reforzar un match léxico existente, o incluir
  algo por sí solo con un umbral alto (0.30) medido para que el ruido de
  fondo (hasta 0.187 en la medición) no lo cruce nunca.

Cada resultado trae un `via` auditable: `synonym`, `lexical`, `embedding`,
`hybrid` (ambos canales de acuerdo), `parent`/`related` (llegó por
taxonomía). `CapabilityEngine.explain(query, agent_id)` expone, por
capacidad candidata, el score léxico, el vectorial, el final, el motivo y el
umbral aplicado — no solo un número.

**Qué mejoró de verdad:** tres sinónimos coloquiales reales (`plata` en
`finance`, `chamba` en `career_transition`, `pegar el ojo` en `fitness`) que
antes no encontraban ningún agente. Medido con `python -m evaluation.bench_routing`
sobre 26 consultas reales/coloquiales: el híbrido **no empeora** la
precisión ni añade falsos positivos frente al léxico solo (ambos 100% en el
set actual) — la ganancia medida viene de los sinónimos añadidos al catálogo
(que benefician a los dos matchers, porque el léxico también los indexa), no
de que el coseno "entienda" la consulta. Sigue habiendo paráfrasis
coloquiales genuinas que ningún canal resuelve todavía (ver ROADMAP).

## Instalar, testear, ejecutar

Requiere **Python 3.10+** (igual que `requires-python` en `pyproject.toml`). Un
checkout limpio se instala y se verifica sin claves, sin red y sin Ollama:

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m pytest
```

```bash
python -m mcp_server.magnus_mcp
```

El extra `dev` es el que trae `pytest`; sin él, `python -m pytest` no encuentra
el runner.

Variables de entorno del servidor:

| Variable | Efecto |
|---|---|
| `MAGNUS_PROVIDER` | vacío → modo extractivo (sin LLM, sin coste). `ollama`, `anthropic` o `auto`. El modelo concreto lo decide el perfil de cada agente contra `configs/models.yaml`. |
| `ANTHROPIC_API_KEY` | credencial del adaptador Anthropic. |
| `MAGNUS_TRACE_DIR` | activa el registro auditable JSONL (desactivado por defecto: la wiki contiene datos personales). |
| `MAGNUS_HTTP_HOST` | bind del servidor HTTP. Por defecto `127.0.0.1`; **cualquier otro valor exige `MAGNUS_HTTP_TOKEN` o el servidor se niega a arrancar**. |
| `MAGNUS_HTTP_TOKEN` | token compartido (`Authorization: Bearer …`). Usa un valor largo y aleatorio. |
| `MAGNUS_HTTP_ORIGINS` | orígenes CORS permitidos, separados por comas. Vacío = ninguno. Nunca se emite `*`. |

### Privacidad

La LLM-Wiki contiene información personal, financiera, legal y de salud. Dos
controles la protegen y ambos deniegan por defecto:

- [`configs/privacy.yaml`](configs/privacy.yaml) decide **qué namespaces pueden
  salir del dispositivo** dentro de un prompt. Salud corporal, salud mental y
  dinámica social están en `local_only`: solo se responden con un proveedor
  local (Ollama), y si no lo hay, el motor degrada a respuesta extractiva y lo
  dice. Un namespace nuevo no sale hasta que se autorice ahí explícitamente.
- [`configs/permissions.yaml`](configs/permissions.yaml) decide **qué parcela
  puede leer cada agente** y qué herramientas puede usar. Se aplica en tiempo
  de ejecución, no es solo documentación.

`python -m mcp_server.magnus_http --port 8765` levanta el mismo servidor sobre
HTTP (`http://127.0.0.1:8765/mcp`) para apps que piden una URL de conector
remoto. El adaptador de Anthropic es una dependencia opcional:
`python -m pip install -e ".[anthropic]"` más `ANTHROPIC_API_KEY`.

### Qué verifica qué

- **`tests/` es la suite de verificación.** Recorre el motor real contra un
  proyecto Magnus mínimo generado en un directorio temporal
  (`tests/magnus_fixtures/`), incluido el circuito completo
  `MagnusEngine → JSON-RPC MCP`.
- **`python -m evaluation.bench_retrieval`** y **`python -m evaluation.bench_routing`**
  miden recuperación y enrutado contra la wiki/agentes reales del
  repositorio, no contra fixtures — y devuelven un código de salida distinto
  de 0 si la estrategia nueva (híbrida) empeora a la anterior (léxica sola).
- **`demo/` NO es la suite de verificación** — es ilustración. `demo/run_demo.py`
  y `demo/prove_principles.py` sustituyen el motor, el proveedor y la wiki por
  maquetas (`demo/fakes.py`) para enseñar el flujo de un vistazo. Que una demo
  pase no dice nada sobre el runtime; para eso está `pytest`.

```bash
python demo/run_demo.py          # ilustración del flujo multiagente
python demo/prove_principles.py  # ilustración: +conocimiento mejora al agente
```

## Conectar Magnus por MCP

Magnus es un sistema multiagente que corre en tu propia máquina, sobre tu
propia wiki. Un cliente MCP (Claude Code, Codex CLI, o cualquier aplicación
que hable el protocolo MCP) no ejecuta Magnus "dentro" de sí mismo: se
conecta como **cliente** y **invoca sus dos herramientas** —`magnus_ask` y
`magnus_list_agents`— igual que invocaría cualquier otro servidor MCP. Magnus
sigue siendo el que decide qué agente responde, qué namespaces puede leer, si
el egreso a un proveedor remoto está permitido y si la respuesta queda
anclada en evidencia — nada de eso lo controla el cliente.

### Transporte stdio (recomendado para uso local)

```bash
python -m mcp_server.magnus_mcp
```

Es el transporte pensado para que el propio cliente lo lance como subproceso
(no lo arrancas tú a mano): habla JSON-RPC delimitado por saltos de línea
sobre `stdin`/`stdout`, no abre ningún socket ni puerto, y por tanto no tiene
superficie de red que asegurar. Es el modo verificado en este documento (ver
más abajo, "Smoke test MCP").

Configuración genérica de un cliente MCP por stdio — **el formato exacto
(nombre de la clave raíz, dónde va el archivo) depende de cada cliente**;
consulta la documentación propia del tuyo:

```json
{
  "mcpServers": {
    "magnus": {
      "command": "python",
      "args": ["-m", "mcp_server.magnus_mcp"],
      "cwd": "C:\\MagnusAgent"
    }
  }
}
```

Este repositorio incluye [`.mcp.json`](.mcp.json) en la raíz — es la
configuración de proyecto real que usa **Claude Code** para levantar este
mismo servidor vía stdio; puedes leerlo como ejemplo concreto ya funcional en
vez de uno hipotético. Para Codex CLI o cualquier otro cliente MCP compatible
con stdio, el protocolo que expone `magnus_mcp.py` es JSON-RPC estándar (lo
mismo que se verifica en el smoke test de abajo); no se documenta aquí una
sintaxis de configuración específica para esos clientes porque no se ha
verificado documentalmente en este repositorio — usa la guía de conexión de
servidores MCP por stdio de tu propio cliente.

Variables de entorno relevantes para este transporte están en la tabla de
["Instalar, testear, ejecutar"](#instalar-testear-ejecutar) más arriba
(`MAGNUS_PROVIDER`, `ANTHROPIC_API_KEY`, `MAGNUS_TRACE_DIR`). Sin ninguna
puesta, Magnus arranca en **modo extractivo**: sin LLM, sin coste, sin salir
a la red — cita pasajes literales de tu wiki.

### Transporte HTTP local (solo si tu cliente exige una URL)

```bash
python -m mcp_server.magnus_http --port 8765
```

Para clientes que piden una URL de "conector remoto" en vez de un comando
local. Expone un único endpoint (`POST http://127.0.0.1:8765/mcp`) y **debes
dejar el proceso corriendo tú mismo** mientras lo uses — a diferencia de
stdio, ningún cliente lo lanza por ti.

**No lo expongas fuera de `127.0.0.1` sin token y sin restringir CORS
explícitamente.** El servidor mismo lo impone: se niega a arrancar en
cualquier interfaz distinta de `127.0.0.1`/`localhost` si `MAGNUS_HTTP_TOKEN`
no está puesto (ver [`mcp_server/http_guard.py`](mcp_server/http_guard.py)).

| Variable | Efecto |
|---|---|
| `MAGNUS_HTTP_HOST` | bind del servidor. Por defecto `127.0.0.1`. |
| `MAGNUS_HTTP_TOKEN` | token compartido (`Authorization: Bearer …`); obligatorio para salir de localhost. Usa un valor largo y aleatorio. |
| `MAGNUS_HTTP_ORIGINS` | orígenes CORS permitidos, separados por comas. Vacío = ninguno; nunca se emite `*`. |

También activos siempre, en local y fuera de local: 1 MiB máximo por
petición, 60 peticiones/minuto por cliente, 8 peticiones concurrentes.

## Guía de validación funcional (pruebas manuales)

Para comprobar a mano que el comportamiento descrito arriba se sostiene en tu
propia wiki, no solo en la de fixtures de `pytest`. Todos los casos se pueden
correr con el servidor stdio en modo extractivo (sin claves, sin red):

```bash
python -m mcp_server.magnus_mcp
```

y enviando `tools/call` → `magnus_ask` con la `pregunta` de cada caso (o
usando tu cliente MCP conectado, ver arriba).

| Caso | Consulta de ejemplo | Resultado esperado | Qué revisar en la traza (`r["traza"]`) |
|---|---|---|---|
| Finanzas | *"cuál es la inflación en República Dominicana"* | Responde `ernesto_libras`, cita al menos una fuente de `01-Economia-y-Finanzas/`, incluye el aviso de "no es asesoría de inversión". | `traza["ernesto_libras"]["guardrails"]["dominios"] == ["finanzas"]`; `chunks_recuperados > 0`. |
| Salud / sueño | *"cuántas horas debo dormir"* | Responde `dr_soma`, cita `02-Salud-Corporal/`, incluye el aviso de "no es una indicación médica". | `traza["dr_soma"]["guardrails"]["dominios"] == ["salud"]`. |
| Sin dominio | *"xyzzy plugh frobnicate"* (o cualquier consulta ajena a la wiki) | `agentes: []`, `fuentes: []`; el texto declara explícitamente que no identificó dominio y lista los agentes disponibles — **no** elige uno arbitrario. | `traza["_enrutado"]["modo"] == "sin_dominio"`; revisar `capacidades_mas_cercanas` para ver qué tan cerca estuvo. |
| Urgencia | *"tengo pensamientos de quitarme la vida"* | Respuesta de contacto humano (911 / profesional), sin RAG ni LLM de por medio. | `traza["_guardrails"]["escalado"]` tiene el id de la urgencia (p. ej. `crisis_salud_mental`); `agentes: []`. |
| Agente forzado | `magnus_ask` con `agente: "lexi"` y cualquier pregunta | Responde siempre `lexi`, salta el umbral de enrutado por completo. | `traza["lexi"]` existe aunque la pregunta no tuviera match léxico claro; no aparece `_enrutado`. |
| Privacidad `local_only` | Pregunta a `serena` o `dr_soma` (namespaces `local_only` en `configs/privacy.yaml`) **con un proveedor remoto real conectado** (`MAGNUS_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` puestos — sin la clave, el modo cae a extractivo por falta de proveedor, no por la política de egreso, y el caso no prueba nada) | La respuesta sigue citando la wiki (modo extractivo), **nunca** llega a llamar al proveedor remoto, aunque el mismo proveedor sí responda sin problema a una pregunta de finanzas en la misma sesión. | `traza[agente]["egreso"]["egreso_remoto"] == False` y `namespaces_que_bloquean`; `modo == "extractivo"`. |
| Error/degradación de proveedor | Con un proveedor configurado que falla (credencial inválida, timeout) | La respuesta declara el fallo explícitamente y ofrece los pasajes literales de la wiki en su lugar — nunca aparenta éxito. | `traza[agente]["modo"] == "extractivo_degradado"`; `error` y `reintentable` presentes. |

## Principio operativo

Añade notas a `LLM-Wiki/wiki/01-Economia-y-Finanzas/` → **Ernesto Libras mejora
automáticamente**, sin modificar su definición. Cambia de proveedor de IA →
edita `configs/models.yaml`, sin tocar ningún agente.

Los cambios al conocimiento son **propuestos por los agentes y aprobados por un
humano** (aprendizaje supervisado, ver Componente 15).
