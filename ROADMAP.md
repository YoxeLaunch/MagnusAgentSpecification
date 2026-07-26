# Roadmap Magnus — de especificación a runtime confiable

Este documento nace de una auditoría del estado real del código (no de la
documentación de diseño) hecha el 2026-07-24. Su propósito es dejar claro,
en un solo lugar, **qué existe, qué falta, y en qué orden se debe construir**
para que Magnus deje de ser una especificación con esqueletos de referencia
y pase a ser un runtime multiagente que funciona de punta a punta.

## Estado actual

**Pasos 1 a 5 completados el 2026-07-25.** Cada paso tiene su sección "Cómo
quedó" más abajo, con lo que se hizo y la deuda que se dejó declarada. El
camino operativo principal funciona de punta a punta y está cubierto por
tests: se hace una pregunta, se comprueba que no haya señal de urgencia, se
calcula la parcela efectiva del agente contra su política, se recupera
evidencia de forma híbrida con el umbral que él declara, se decide si esos
namespaces pueden salir del dispositivo, se genera con el modelo de su perfil,
se verifica que la respuesta esté anclada en la evidencia y se registra todo
de forma auditable.

**Además, del paso 6 (expansión), el primer bloque —enrutado semántico local
(`EmbeddingCapabilityMatcher` + `HybridCapabilityMatcher`)— está completado y
verificado desde el 2026-07-25** (ver su sección más abajo). El resto del
paso 6 sigue sin empezar.

**Deuda declarada, por si se lee esta línea sin leer el resto:**

| Pendiente | Paso | Por qué no se hizo |
|---|---|---|
| Cancelación cooperativa de consultas | 2 | Exige un modelo de ejecución asíncrono que no existe |
| Evaluador LLM-as-judge contra `rubric_ref` | 3 | El puerto está listo; tiene coste y latencia reales |
| Embeddings neuronales (bge-m3) y reranker cross-encoder | 4 | Dependencia pesada (torch, ~2 GB) que se decidió evitar |
| Redacción de datos sensibles dentro del prompt | 5 | Exige un clasificador; un regex daría falsa seguridad |
| Retención/rotación del registro de auditoría | 5 | Crece sin límite, se borra a mano |
| Embeddings neuronales para enrutado; paráfrasis coloquiales sin vocabulario compartido | 6 (bloque 1) | El coseno local demostró ser más débil que el léxico en el corpus de capacidades; resolverlo de verdad exige un embedder neuronal |
| `_STOP` de `LexicalCapabilityMatcher` no filtra demostrativos ("este", "esta"…) | 6 (bloque 1, hallazgo) | Fuera de alcance de este bloque; candidato de limpieza de bajo riesgo |

El evaluador comprueba que la respuesta esté **anclada**, no que sea
**correcta**: una respuesta que cita bien y razona mal pasa.

### Corrección posterior — enrutado seguro (2026-07-25)

Detectada al verificar los pasos 1-5 de punta a punta, no cubierta por ninguno
de ellos. Cuando ninguna capacidad alcanzaba el umbral,
`MagnusEngine._route` devolvía `registry.list(status="active")[:1]`: **el
primer agente activo por orden alfabético**. Eso contestaba una consulta de
salud con el agente de finanzas porque su `id` empieza por 'a', recuperaba de
la parcela equivocada y presentaba el resultado con la misma apariencia de
fundamento que una respuesta bien enrutada. Caso real observado:
*"que dice mi wiki sobre como dormir mejor"* → `amanda` (proyectos).

Ahora el motor responde explícitamente que no identificó dominio, enumera los
agentes disponibles para que la consulta se pueda dirigir a mano, no consulta a
ningún modelo y deja en la traza el umbral y las capacidades más cercanas.
Forzar un agente por `id` sigue funcionando y salta el umbral, porque ahí no
hay nada que enrutar. El umbral es un parámetro explícito
(`MagnusEngine(routing_min_score=...)`), no una constante escondida.

Cubierto por `tests/test_routing.py` (match válido, sin match, agente forzado,
umbral configurable, y que una señal de urgencia siga escalando aunque el
enrutado no identifique dominio — la seguridad no puede depender de que el
enrutado acierte).

**Lo que esto NO arregla:** la precisión del enrutado léxico. Antes fallaba en
silencio; ahora falla diciéndolo. Un `EmbeddingCapabilityMatcher` sobre el
mismo puerto `CapabilityMatcher` es trabajo del paso 6.

### Verificación de punta a punta y guía de conexión MCP (2026-07-25)

Pase de validación de producto sobre los pasos 1-5 ya cerrados, sin abrir
trabajo nuevo del paso 6. Resultado exacto:

- `python -m pip install -e ".[dev]"` + `python -m pytest` → **161 passed**,
  sin fallos que diagnosticar.
- `python -m evaluation.bench_retrieval` → recall@8 léxico 89.5%, vectorial
  local 73.7%, híbrido 94.7% (sin cambios respecto al paso 4; exit code 0).
- Smoke test manual del servidor stdio (`python -m mcp_server.magnus_mcp`,
  sin `ANTHROPIC_API_KEY` ni `MAGNUS_PROVIDER`, JSON-RPC por stdin/stdout, sin
  abrir socket): `initialize`, `tools/list`, `magnus_list_agents`, una
  consulta de finanzas (enruta a `ernesto_libras`, cita fuentes, trae el aviso
  de dominio), una de salud/sueño (`dr_soma`, mismo patrón), una sin dominio
  (`agentes: []`, se declara explícitamente en vez de adivinar) y una de
  urgencia (escala sin tocar RAG ni proveedor). Los siete casos se comportan
  como documentan los pasos 3 y 5 y la corrección de enrutado de arriba —
  ningún hallazgo nuevo.
- Se añadió al README la sección **"Conectar Magnus por MCP"** (cómo un
  cliente MCP como Claude Code invoca a Magnus por stdio o HTTP local, con la
  configuración de ejemplo genérica y el `.mcp.json` real del repo como caso
  concreto verificado) y una **guía de validación manual** con 7 casos
  (finanzas, salud/sueño, sin dominio, urgencia, agente forzado, privacidad
  `local_only`, degradación de proveedor) con el resultado esperado y qué
  campo de la traza revisar en cada uno. Dos de esos casos (privacidad
  `local_only` y degradación de proveedor) se verificaron ejecutándolos antes
  de documentarlos, no solo se describieron de memoria.
- No se afirma compatibilidad verificada con Codex CLI ni con ningún cliente
  MCP concreto más allá de Claude Code (para el que el propio repo trae
  `.mcp.json` como evidencia): el servidor stdio habla JSON-RPC estándar, y
  cualquier cliente MCP compatible con stdio debería poder conectarse, pero
  eso no se probó documentalmente aquí.

### El diagnóstico original (2026-07-24)

Magnus tenía una arquitectura y especificación de producto avanzada —
`docs/`, `agents/*/agent.yaml` con validación estructural y referencial real,
`capabilities/*.yaml`, un Agent Registry funcional con herencia — y varios
componentes de referencia genuinamente implementados (parsing de agentes,
enrutado por capacidades, ingesta de la wiki). **Pero el camino operativo
principal — hacer una pregunta real y obtener una respuesta evaluada, citada,
generada por un proveedor de IA de verdad — era todavía un prototipo.**

La causa raíz común a casi todos los hallazgos: **la documentación y las
demos (`demo/`) estaban más adelantadas que `orchestration/engine.py`**, que es
el único camino que consumen `mcp_server/` y `sdk/cli.py` en producción. Las
demos simulaban (evaluador, citas, proveedor fake) lo que el motor real no
hacía.

## Principio de orden

No añadir más agentes, más proveedores, ni más features nuevas (planner,
memoria persistente, eventos) hasta que el circuito ya diseñado —
`MagnusEngine → CapabilityEngine → RAGPipeline → ProviderRegistry → adaptador`
— sea correcto y esté cubierto por tests. Expandir superficie sobre una base
rota multiplica el trabajo de arreglarla después.

---

## Paso 1 — Empaquetado, dependencias y arnés de tests ✅ COMPLETADO

**Por qué primero:** sin esto, ningún otro paso es verificable de forma
reproducible ni revisable por otra persona (o por CI).

**Diagnóstico original (2026-07-24), que este paso vino a corregir:**
- No existe `pyproject.toml`, `requirements.txt` ni `setup.cfg` en la raíz.
- El proyecto no es un repositorio git (`git init` pendiente).
- `AgentRegistry`, `MagnusEngine` y `FileWikiStore` dependen de `PyYAML`
  (`import yaml` en [`orchestration/registry/agent_registry.py`](orchestration/registry/agent_registry.py)
  y [`providers/registry.py`](providers/registry.py)) sin que esa dependencia
  esté declarada en ningún archivo.
- No hay ningún archivo con "test" en el nombre fuera de `LLM-Wiki/` (que es
  contenido, no código). La única verificación existente son
  `demo/run_demo.py` y `demo/prove_principles.py`, que usan proveedores y
  wiki *fake* (`demo/fakes.py`), no el motor real contra la wiki real.

**Qué hacer:**
1. `git init` + primer commit del estado actual.
2. `pyproject.toml` con dependencias mínimas (`PyYAML`; `anthropic` como
   extra opcional para el adaptador Anthropic) y versión de Python fijada
   (el código usa `from __future__ import annotations` y sintaxis `X | None`,
   requiere 3.10+; el README dice 3.12+).
3. Elegir runner de tests (`pytest`) y crear `tests/` con un layout que
   refleje `orchestration/`, `providers/`, `kernel/rag/`.
4. Un comando único para instalar, uno para testear y uno para ejecutar.
5. Añadir fixtures offline mínimos (wiki pequeña, agentes/capacidades YAML y
   proveedor fake compatible con `ProviderRegistry`) y pruebas de integración
   que recorran Engine → JSON-RPC MCP sin red ni credenciales.
6. Mover las demos a `examples/` o dejarlas en `demo/` pero documentar
   explícitamente que **no son la suite de verificación** — son ilustración.

**Criterio de hecho:** en un checkout limpio, y en una máquina sin
`ANTHROPIC_API_KEY` ni Ollama, esto corre sin tocar nada a mano:

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m pytest
```

`pytest` a secas **no** basta: el runner de tests vive en el extra `dev`
(`pyproject.toml`), no en las dependencias obligatorias, y `python -m` evita
usar un `pip`/`pytest` de otro intérprete distinto al del entorno activo.

**Cómo quedó (2026-07-25):**
- Repositorio git inicializado con un commit de línea base del estado auditado.
- [`pyproject.toml`](pyproject.toml) declara `PyYAML` como única dependencia
  obligatoria; `anthropic` y `pytest` son extras (`.[anthropic]`, `.[dev]`).
  `requires-python = ">=3.10"` — el mínimo real del código, no el 3.12+ que
  decía el README sin que nada lo exigiera.
- `tests/` con `pytest`, fixtures offline en `tests/magnus_fixtures/`:
  `build_mini_project()` genera un proyecto Magnus completo (3 agentes con
  herencia, 2 capacidades, configs, wiki de 3 notas) en `tmp_path`, y
  `FakeProvider` implementa la firma real del puerto para enchufarse a un
  `ProviderRegistry` de verdad.
- `conftest.py` borra del entorno toda clave de proveedor: si un test intentara
  salir a la red, falla en vez de gastar dinero en silencio.
- Test de integración `tests/test_mcp_protocol.py`: JSON-RPC MCP → `MagnusEngine`
  → wiki, sin sockets, sin red, sin credenciales. Requirió hacer perezoso el
  motor en [`mcp_server/protocol.py`](mcp_server/protocol.py) (`get_engine()` /
  `set_engine()`), que antes se construía en tiempo de import.
- `mcp_server` es ahora un paquete: `python -m mcp_server.magnus_mcp` y
  `python -m mcp_server.magnus_http` funcionan, además de los scripts
  `magnus`, `magnus-mcp` y `magnus-mcp-http`.
- Las demos se quedaron en `demo/` con un aviso en cabecera de cada archivo:
  **no son la suite de verificación**, corren sobre maquetas.

---

## Paso 2 — El motor respeta la configuración real del agente ✅ COMPLETADO

**Por qué segundo:** es el defecto más grave del runtime — el modo con LLM
real está roto, y aunque no lo estuviera, ignoraría toda la configuración
por-agente que ya existe en `agent.yaml` y que el Agent Registry ya valida
y expone.

**Diagnóstico original (2026-07-24), que este paso vino a corregir:**

- [`orchestration/engine.py:117`](orchestration/engine.py) llama:
  ```python
  resp = self.provider.complete(LLMRequest(...))
  ```
  pero el puerto `LLMProvider.complete` ([`providers/base.py:104`](providers/base.py))
  exige **dos** argumentos: `complete(self, req: LLMRequest, resolved: ResolvedModel)`.
  Con cualquier proveedor real conectado (`AnthropicProvider`, `OllamaProvider`),
  esta llamada lanza `TypeError` de inmediato. **El modo LLM del motor no
  funciona hoy, no es que esté mal configurado — está roto.**
- La misma línea fuerza `profile="local_private"` hardcodeado, ignorando
  `agent.model_profile` y `agent.fallback_profile`, que `AgentSpec` ya
  expone ([`orchestration/registry/agent_registry.py:56-57`](orchestration/registry/agent_registry.py)).
- `ProviderRegistry` ([`providers/registry.py`](providers/registry.py)), que
  resuelve perfil → `(provider, model, params)` con fallback automático, existe
  y funciona de forma aislada, pero **`MagnusEngine` nunca lo instancia ni lo
  recibe** — no hay ningún punto del código de producción que lo use.
- [`engine.py:94`](orchestration/engine.py) usa `min_score=self.min_score`
  (un valor global del motor) en vez de `a.min_score` (el umbral que cada
  agente declara en `knowledge.retrieval.min_score` de su `agent.yaml`, y
  que `AgentSpec.min_score` ya expone). `top_k` sí se respeta correctamente
  (`a.top_k or self.top_k`) — es el único campo por-agente que el motor
  ya honra hoy.
- `models.yaml` declara perfiles con `provider: openai`, `provider: mistral`,
  `provider: openrouter` como fallback (p. ej. `reasoning_high` →
  fallback `openai/gpt-5`), pero solo existen adaptadores para `anthropic` y
  `ollama`. Cualquier fallback a esos proveedores explota con `KeyError` en
  [`providers/registry.py:45,50`](providers/registry.py).

**Qué hacer:**
1. Inyectar `ProviderRegistry` en `MagnusEngine.__init__` (construido desde
   `configs/models.yaml` + diccionario de adaptadores disponibles).
2. Cambiar `_agent_answer` para llamar `self.providers.complete(req)` (con
   `req.profile = a.model_profile`), dejando que `ProviderRegistry` resuelva
   provider/model/fallback — nunca hardcodear un perfil en el motor.
3. Definir y probar la política de fallback antes de implementarla: separar el
   fallback interno de un perfil (`models.yaml`) del `fallback_profile` del
   agente (`agent.yaml`), decidir cuál gana y qué errores autorizan failover.
   En general, indisponibilidad, timeout o rate limit pueden usar fallback;
   credenciales inválidas y solicitudes inválidas deben fallar con un error
   claro, no ocultarse cambiando de proveedor.
4. Cambiar `RAGRequest(min_score=self.min_score, ...)` a
   `RAGRequest(min_score=a.min_score, ...)`.
5. Para los perfiles que referencian `openai`/`mistral`/`google`/`openrouter`:
   o se implementan esos adaptadores (ver paso 6), o se marcan como
   `unavailable` en `models.yaml` con un mecanismo que falle rápido y con
   mensaje claro en vez de `KeyError` genérico.
6. Normalizar fallos operativos: timeout, reintentos acotados, cancelación,
   límites de tamaño de contexto y una degradación extractiva explícita cuando
   no haya proveedor disponible. Registrar por consulta el perfil solicitado,
   proveedor/modelo final, fallback aplicado, latencia y causa del error.

**Criterio de hecho:** con `ANTHROPIC_API_KEY` configurada y `MAGNUS_PROVIDER`
apuntando a Anthropic, una consulta real a través de `mcp_server/protocol.py`
produce una respuesta generada por el modelo del perfil que el agente
concreto declara; la traza permite verificar perfil, modelo final y si hubo
fallback. Los fallos de proveedor devuelven un error normalizado o la
degradación declarada, nunca un `TypeError`/`KeyError` crudo.

**Cómo quedó (2026-07-25):**
- `MagnusEngine` recibe `providers: ProviderRegistry`. Un adaptador suelto
  (`provider=`) se sigue aceptando pero se **envuelve** en un registro, así el
  motor tiene un solo camino de ejecución.
- `_agent_answer` construye `LLMRequest(profile=a.model_profile, effort=a.effort)`
  y llama `providers.complete_with_trace(req, fallback_profile=a.fallback_profile)`.
  Ni el perfil ni el modelo se deciden ya en el motor.
- `RAGRequest(min_score=a.min_score, ...)`. Medido antes de cambiarlo: los
  scores léxicos reales de la wiki van de 0.4 a 1.0, así que los umbrales
  declarados (0.30–0.35) son alcanzables — el 0.02 global solo dejaba pasar
  ruido. Hay un test que fija esto con un chunk de score 0.205.
- **Política de fallback** (documentada en el docstring de
  [`providers/registry.py`](providers/registry.py)): primario → `fallback` del
  perfil (`models.yaml`) → `fallback_profile` del agente (`agent.yaml`). El
  sustituto equivalente va antes que la degradación de capacidad. Solo los
  errores `retryable` (indisponibilidad, timeout, 429, 5xx) autorizan failover;
  401/403/400 fallan de inmediato con el error normalizado.
- `models.yaml`: se retiraron los fallbacks a `openai`/`mistral`/`openrouter`,
  que prometían una red de seguridad inexistente, y el perfil `broad`
  (`openrouter`, sin adaptador y sin agentes que lo usaran). Los proveedores
  sin adaptador quedan declarados con `adapter: pendiente` y ningún perfil los
  referencia. Un perfil que resuelva a un proveedor sin adaptador lanza
  `ProviderUnavailable` con la lista de adaptadores cargados, no `KeyError`;
  un perfil inexistente lanza `ProfileNotFound` con la lista de perfiles.
- **Resiliencia:** reintentos acotados con backoff exponencial (inyectable, la
  suite no duerme), timeout explícito en ambos adaptadores, techo de contexto
  (`MAX_CONTEXT_CHARS`, corta chunks enteros para no romper la trazabilidad de
  una cita) y degradación extractiva **declarada** — la respuesta dice que la
  generación falló y por qué, en vez de aparentar éxito. `on_provider_error="raise"`
  propaga el error normalizado si se prefiere fallar duro.
- **Traza por consulta** (`ProviderTrace`): perfil solicitado, cada intento con
  proveedor/modelo/tipo/latencia/error, proveedor y modelo finales, y si hubo
  fallback. Se devuelve en `ask()["traza"]` y se emite por el logger
  `magnus.engine`.
- Bug adyacente corregido: el adaptador Anthropic volcaba `resolved.params` en
  crudo al payload, así que el perfil `reasoning_high` mandaba
  `thinking: "adaptive"` (string en vez de objeto) y `effort` a nivel superior
  — justo lo que el docstring del propio adaptador prohíbe. Ahora `thinking` y
  `effort` se traducen como campos canónicos y lo que declara el agente gana
  sobre el valor del perfil. Los timeouts sin `status_code` pasan a ser
  reintentables (antes abortaban sin darle turno al fallback).

**Lo que NO quedó hecho de este paso:** la cancelación cooperativa de una
consulta en vuelo. Requiere un modelo de ejecución asíncrono o con hilos que
hoy no existe (el motor es síncrono de punta a punta); los timeouts por
adaptador acotan el daño mientras tanto. Queda anotado en el paso 6.

---

## Paso 3 — Evaluación y verificación programática de citas ✅ COMPLETADO

**Por qué tercero:** en dominios como finanzas, medicina y salud mental
(exactamente los que cubren `ernesto_libras`, `dr_soma`, `amanda`), una
respuesta sin verificación de evidencia no es un detalle de calidad — es un
riesgo real de producto. Va después del paso 2 porque no tiene sentido
evaluar respuestas de un motor cuyo camino con LLM todavía no ejecuta.

**Diagnóstico original (2026-07-24), que este paso vino a corregir:**

- [`engine.py:95`](orchestration/engine.py) fuerza `require_citations=False`
  en cada `RAGRequest`, **pese a que cada agente ya declara**
  `evaluation.require_citations` en su `agent.yaml`, y `AgentSpec.require_citations`
  ya lo expone ([`agent_registry.py:64,310`](orchestration/registry/agent_registry.py)).
  El campo existe, se valida al cargar el agente, y el motor lo ignora.
- No existe ningún evaluador en el runtime real. `demo/` simula uno; en
  `orchestration/` no hay módulo de evaluación que se ejecute contra
  `evaluation.rubric_ref` / `evaluation.rigor` de cada agente.
- `RAGPipeline.build_context` ([`kernel/rag/pipeline.py:95-97`](kernel/rag/pipeline.py))
  sí tiene la lógica correcta para *rechazar* contexto sin evidencia cuando
  `require_citations=True` — está lista para usarse, solo que el motor nunca
  se lo pide.

**Qué hacer:**
1. Cambiar `engine.py` para pasar `require_citations=a.require_citations`.
2. Diseñar e implementar un evaluador mínimo que, para cada respuesta
   generada por LLM, verifique que las afirmaciones relevantes tengan
   evidencia recuperada — puede empezar como una verificación estructural
   (¿la respuesta cita al menos una fuente de `ctx.citations`?) antes de
   escalar a algo más sofisticado (LLM-as-judge contra `rubric_ref`).
3. Definir la política de manejo cuando la evaluación falla: rechazar y
   responder con incertidumbre explícita, pedir aclaración, o escalar a
   revisión humana — nunca devolver la respuesta débil silenciosamente.
4. Añadir guardrails por dominio: límites explícitos para consejo médico y
   financiero, advertencias contextuales y una vía de escalado ante señales de
   urgencia. Una cita válida no vuelve segura una recomendación inapropiada.
5. Guardar la decisión de evaluación (score, pass/fail, razón), los hashes de
   los documentos/chunks recuperados y el snapshot o commit de la wiki en un
   registro trazable. `wiki-live` por sí solo no permite reproducir una cita
   después de editar la nota.
6. Tests que cubran: agente con `require_citations=True` sin evidencia →
   la respuesta se marca como rechazada/escalada, no como éxito silencioso.

**Criterio de hecho:** una consulta a un agente con `require_citations: true`
y sin evidencia suficiente en la wiki produce una respuesta que declara la
incertidumbre explícitamente, no una alucinación con apariencia de éxito; una
cita publicada se puede reproducir desde su hash y versión de conocimiento.

**Cómo quedó (2026-07-25):**
- `engine.py` pasa `require_citations=a.require_citations`. El campo llevaba
  existiendo y validándose desde el principio; ahora además se usa.
- **Evaluador** en [`orchestration/evaluation/citation_evaluator.py`](orchestration/evaluation/citation_evaluator.py):
  verificación estructural, determinista, sin red ni coste. Detecta dos fallos
  distintos: respuesta **sin cita** (indistinguible de una inventada) y **cita
  fabricada** — un `[...]` que referencia algo que no estaba en la evidencia,
  que es peor porque aparenta fundamento. `evaluation.rigor >= 8` exige dos
  fuentes distintas cuando hubo al menos dos disponibles. `Evaluator` es un
  `Protocol`: un LLM-as-judge contra `rubric_ref` se enchufa sin tocar el motor.
- **Política ante fallo** (`_aplicar_politica`): agente con
  `require_citations: true` → la respuesta generada se **rechaza** y se declara
  la incertidumbre, ofreciendo en su lugar los pasajes literales de la wiki
  (que sí están anclados); agente sin esa exigencia → la respuesta se conserva
  pero se marca de forma **visible**, no solo en la traza. Nunca se devuelve la
  respuesta débil en silencio.
- **Guardrails por dominio** en [`configs/guardrails.yaml`](configs/guardrails.yaml)
  (configuración, no código, por la misma razón que `models.yaml`). El dominio
  se deduce de las capacidades que declara el agente, no de palabras clave en
  la pregunta. El aviso entra tanto en el system prompt como anexado a la
  respuesta.
- **Escalado por urgencia**: se comprueba ANTES de recuperar evidencia o llamar
  a ningún modelo y corta el flujo — ante una señal de crisis la respuesta
  correcta no es una respuesta mejor documentada, es una vía de contacto
  humano. Aplica aunque el enrutado eligiera un agente de otro dominio: una
  crisis no deja de serlo porque la consulta cayera en finanzas.
- **Trazabilidad reproducible**: `FileWikiStore` calcula un `snapshot_id` (digest
  del contenido de todas las notas indexadas) y un hash por pasaje. La
  `Provenance` pasa de `knowledge_version: "wiki-live"` —una etiqueta constante
  que no permitía reproducir nada— a `wiki:<snapshot>` más el hash del chunk:
  si la nota cambió desde que se emitió la cita, se sabe.
- **Registro auditable** en [`orchestration/audit.py`](orchestration/audit.py)
  (JSONL): decisión de evaluación con score/razón, ids y hashes de los chunks,
  snapshot de la wiki, guardrails aplicados y proveedor/modelo final.
  **Desactivado por defecto** — la wiki contiene datos personales, financieros
  y de salud, así que escribir a disco es opt-in (`MAGNUS_TRACE_DIR`), y se
  guardan referencias, nunca el texto de los pasajes.

**Deuda consciente de este paso:** el evaluador es estructural — comprueba que
la respuesta esté *anclada*, no que sea *correcta*. Una respuesta que cita bien
y razona mal pasa. El escalón siguiente (LLM-as-judge contra `rubric_ref`, con
su coste y su latencia) tiene el puerto listo y no está implementado.

---

## Paso 4 — RAG realmente híbrido (léxico + vectorial local) ✅ COMPLETADO

**Por qué cuarto:** el RAG actual "funciona" en el sentido de que devuelve
resultados plausibles por solape de palabras, así que no bloquea los pasos
2-3. Pero mientras el motor lo llame "híbrido" sin serlo, cualquier mejora
de evaluación (paso 3) estará evaluando sobre una base de recuperación más
débil de lo que aparenta.

**Diagnóstico original (2026-07-24), que este paso vino a corregir:**

- [`engine.py:59`](orchestration/engine.py):
  ```python
  self.rag = RAGPipeline(self.store, self.store)  # denso+léxico = mismo store
  ```
  El mismo `FileWikiStore` —recuperación léxica pura por solape de tokens
  normalizados, ver [`kernel/rag/file_store.py:107-127`](kernel/rag/file_store.py)—
  se pasa como si fuera el retriever denso *y* el léxico. No hay ningún
  embedding en el repositorio.
- `models.yaml` ya declara una sección `embeddings.default` (`provider: local,
  model: bge-m3, dim: 1024`) que hoy no la consume ningún componente.
- `RAGPipeline` ([`kernel/rag/pipeline.py`](kernel/rag/pipeline.py)) ya tiene
  el contrato correcto (`DenseRetriever` / `LexicalRetriever` / `Reranker`
  como `Protocol`s intercambiables) — el diseño está listo para recibir un
  segundo retriever real sin cambiar la orquestación.

**Qué hacer:**
1. Implementar un `Embedder` real (puerto ya definido en
   [`providers/base.py:110-115`](providers/base.py)) — local (bge-m3 vía
   sentence-transformers, o similar) para no depender de red/coste.
2. Construir un vector store local (o Qdrant si se quiere producción) que
   implemente el contrato `DenseRetriever`.
3. Dejar `FileWikiStore` como el retriever léxico (rol correcto: es TF sobre
   tokens, eso es exactamente recuperación léxica) y usarlo solo ahí.
4. Añadir un reranker opcional (cross-encoder) — el contrato `Reranker` ya
   existe en el pipeline.
5. Construir un pequeño set de preguntas de evaluación de recuperación
   (goldens: query → chunks esperados) por namespace, para medir recall/precision
   antes/después del cambio.

**Criterio de hecho:** `RAGPipeline` recibe dos retrievers distintos con
implementaciones distintas, y el set de evaluación de recuperación muestra
mejora medible frente al baseline solo-léxico.

**Cómo quedó (2026-07-25):**

Medido sobre la wiki real (117 notas, 1162 chunks) con 19 goldens,
`python -m evaluation.bench_retrieval`:

| Configuración | recall@8 |
|---|---|
| léxico solo (baseline) | 89.5% |
| vectorial local solo | 73.7% |
| pipeline léxico+léxico (lo que había) | 89.5% |
| **pipeline híbrido léxico+vectorial (ahora)** | **94.7%** |

**Qué es y qué no es lo implementado.** El segundo retriever es **vectorial
local por random indexing con pesos TF-IDF**, no un embedder neuronal. No hay
`bge-m3`, sentence-transformers, torch ni Qdrant en el repositorio. Llamarlo
"embeddings" a secas repetiría el error que este roadmap vino a corregir:
describir el sistema por lo que se pretende que sea. El puerto `Embedder`
queda listo para el embedder neuronal, que es trabajo del paso 6.

- [`kernel/rag/embedder.py`](kernel/rag/embedder.py) — `HashingEmbedder`,
  implementación real del puerto `Embedder`: random indexing con pesos TF-IDF
  sobre unigramas, bigramas y prefijos. **No es un embedding neuronal** y el
  docstring lo dice sin rodeos: no conoce sinónimos que no compartan forma
  ("paro"/"desempleo" siguen sin parecerse). Lo que aporta frente al léxico
  existente es IDF, bigramas, prefijos (importa mucho en español) y coseno
  normalizado. bge-m3 vía sentence-transformers se enchufa en el mismo puerto;
  se evitó meter torch y ~2 GB de descarga en un proyecto que corre entero con
  la biblioteca estándar.
- [`kernel/rag/vector_store.py`](kernel/rag/vector_store.py) —
  `InMemoryVectorStore`, contrato `DenseRetriever`. Se construye sobre los
  **mismos chunks** del store léxico, así comparten `chunk_id` y el pipeline
  puede deduplicar. Coste medido: +2.3 s de arranque, 17 ms por consulta.
- `FileWikiStore` queda como retriever léxico, su rol correcto, y expone
  `documents()` para que el índice denso reutilice su troceado.
- **Dos correcciones de fusión que el cambio destapó:**
  1. Se fusionaba con `max(score)` entre las dos listas, comparando un coseno
     con un TF léxico — escalas distintas. Ahora el **orden** lo decide
     Reciprocal Rank Fusion (posiciones, que sí son comparables) y el
     **umbral** se aplica sobre el score original de cada retriever, que es la
     escala en la que el agente calibró su `min_score`.
  2. A cada retriever se le pedían solo `top_k` candidatos. Fusionar dos listas
     cortas **empeoraba** el baseline (84.2% con sobremuestreo 1, frente a
     89.5% del léxico solo): el retriever más débil desplazaba aciertos del más
     fuerte. Con sobremuestreo el recall sube monótonamente (1→84.2%, 2-4→89.5%,
     6-8→94.7%, 10→100%); se fijó 8, en la meseta. **No se eligió 10**: con 19
     goldens, exprimir el último punto es ajustar el parámetro al set de
     evaluación, no mejorar la recuperación.
- Banco de recuperación en [`evaluation/bench_retrieval.py`](evaluation/bench_retrieval.py)
  con goldens en [`evaluation/goldens/retrieval.yaml`](evaluation/goldens/retrieval.yaml).
  Las consultas están escritas como las escribiría el usuario, no copiando el
  título de la nota — un golden que repite el título no mide nada. Hay además
  un test que fija "el híbrido no empeora el baseline" sin clavar un número:
  el recall depende del contenido de la wiki, que el usuario edita.

**Lo que NO quedó hecho de este paso:** el reranker cross-encoder (punto 4.4).
El contrato `Reranker` existe y el pipeline lo acepta, pero implementarlo exige
un modelo — la misma dependencia pesada que se decidió evitar. El único caso
que sigue fallando en el banco ("por qué unos países son mucho más ricos que
otros") recupera notas genuinamente relevantes, solo que no la concreta que
espera el golden; se dejó el golden sin tocar en vez de reescribirlo tras ver
el resultado.

---

## Paso 5 — Enforcement de permisos, auditoría y autenticación MCP ✅ COMPLETADO

**Por qué quinto:** hoy el riesgo real es acotado porque el servidor MCP
solo expone dos herramientas fijas (`magnus_ask`, `magnus_list_agents`) sin
ejecución de herramientas arbitrarias — no hay superficie de ataque grande
todavía. Pero es la última pieza que debe cerrarse **antes** de exponer el
servidor fuera de `127.0.0.1` o de ampliar el catálogo de herramientas MCP.

**Diagnóstico original (2026-07-24), que este paso vino a corregir:**

- `configs/permissions.yaml` define políticas (`knowledge.read/write`,
  `tools.allow/deny`, `actions.external_side_effects`) por agente, y
  `AgentRegistry._validate_referential` ([`agent_registry.py:254-256`](orchestration/registry/agent_registry.py))
  valida que `permissions.policy_ref` exista al cargar el agente. **Eso es
  toda la aplicación que existe hoy** — se comprueba que la referencia sea
  válida, nunca se aplica la política en tiempo de ejecución.
- [`mcp_server/protocol.py`](mcp_server/protocol.py) despacha `tools/call`
  directo a `call_tool()` sin ninguna capa de permisos, usuario o agente
  intermedia — no hay noción de "quién" está llamando.
- [`mcp_server/magnus_http.py:37,46`](mcp_server/magnus_http.py) responde con
  `Access-Control-Allow-Origin: *` en todas las respuestas y no implementa
  ninguna autenticación — mitigado hoy por atarse a `127.0.0.1`, pero es
  una bomba de tiempo si alguien cambia el bind address sin revisar esto.
- No hay límites de tasa (rate limiting) en ningún punto.

**Qué hacer:**
1. Diseñar una capa de enforcement que, antes de ejecutar cualquier
   herramienta o de acceder a un namespace de conocimiento, consulte
   `permissions.yaml` vía la política del agente activo. Definir un único
   permiso efectivo: la intersección de `agent.yaml::tools`, la política de
   permisos y la autorización del usuario llamante; cualquier desacuerdo se
   deniega por defecto.
2. Añadir autenticación mínima al servidor HTTP (token compartido, al menos)
   antes de que se pueda considerar exponerlo fuera de localhost.
3. Restringir CORS a orígenes explícitos en vez de `*`.
4. Definir una política de salida de datos: qué namespaces pueden enviarse a
   proveedores remotos, consentimiento del usuario, redacción de datos
   sensibles y tratamiento/retención protegida de prompts y logs de auditoría.
   La LLM-Wiki contiene información potencialmente personal, financiera y de
   salud; no debe salir del dispositivo por una configuración implícita.
5. Añadir auditoría: quién (agente/usuario), qué herramienta, cuándo, con
   qué resultado — reusando el mismo registro trazable del paso 3 y evitando
   registrar contenido sensible sin la protección definida arriba.
6. Rate limiting básico en el transporte HTTP, con límites de tamaño de
   petición y de concurrencia.

**Criterio de hecho:** un intento de un agente de leer un namespace fuera de
su `permissions.knowledge.read`, o de usar una herramienta no autorizada por
la política efectiva, es rechazado por el motor, no solo por convención de
diseño; el servidor HTTP exige autenticación si el bind address deja de ser
`127.0.0.1`, y el envío de contexto sensible a un proveedor remoto requiere
la política y el consentimiento configurados.

**Cómo quedó (2026-07-25):**
- [`orchestration/permissions.py`](orchestration/permissions.py) — `PermissionEngine`.
  El permiso efectivo es la **intersección de tres fuentes**: lo que declara el
  `agent.yaml`, lo que concede su política y lo que autoriza el rol del
  llamante. Cualquier desacuerdo deniega, y `deny` gana sobre `allow` en
  cualquier nivel. Una política inexistente no concede nada.
- El motor calcula la parcela efectiva **antes** de recuperar: si la política no
  concede ninguno de los `knowledge.sources` del agente, la consulta se deniega
  con el motivo, en vez de responder desde una parcela que no le corresponde.
- **Deriva que este paso destapó:** `economist_readonly` concedía lectura sobre
  `economics/`, `finance/`, `imf/`… — los namespaces de la wiki *antes* de
  reorganizarla en carpetas numeradas. `ernesto_libras` declara
  `01-Economia-y-Finanzas`. Con enforcement real ese agente se quedaba sin leer
  absolutamente nada. Corregido, y con un test que falla si vuelve a divergir:
  es justo el tipo de deriva que una política escrita pero nunca aplicada
  acumula sin que nadie se entere.
- **Política de egreso** en [`configs/privacy.yaml`](configs/privacy.yaml) +
  [`orchestration/privacy.py`](orchestration/privacy.py). Denegación por
  defecto: un namespace que nadie autorizó no sale del dispositivo, y eso
  incluye las carpetas nuevas que aparezcan en la wiki. Salud corporal, salud
  mental y dinámica social quedan `local_only`. Editar ese archivo **es** el
  acto de consentimiento. `ProviderRegistry.complete(..., only_local=True)`
  poda los proveedores remotos del plan, así que el fallback automático no
  puede ser la puerta trasera del egreso; si no hay endpoint local, se degrada
  a extractivo y se declara.
- **Servidor HTTP** ([`mcp_server/http_guard.py`](mcp_server/http_guard.py),
  separado del transporte para poder testearlo sin abrir un socket):
  - Se **niega a arrancar** fuera de `127.0.0.1` sin `MAGNUS_HTTP_TOKEN`.
    Antes, lo único que evitaba el agujero era que nadie hubiera cambiado esa
    línea.
  - CORS: se acabó el `Access-Control-Allow-Origin: *` que autorizaba a
    cualquier página abierta en el navegador a interrogar la wiki personal.
    Ahora solo se refleja un origen listado en `MAGNUS_HTTP_ORIGINS`; nunca `*`.
  - Token comparado en tiempo constante (`hmac.compare_digest`).
  - Límites siempre activos, también en localhost: 1 MiB por petición,
    60 peticiones/minuto por cliente, 8 peticiones concurrentes.
  - Verificado en vivo: `200` normal, sin cabecera CORS para un origen
    arbitrario, `413` por tamaño, `429` al superar la tasa, y arranque abortado
    en `0.0.0.0` sin token.
- **Auditoría de herramientas** en `mcp_server/protocol.py`: cada `tools/call`
  registra herramienta, rol, marca de tiempo y resultado, reusando el registro
  trazable del paso 3 y su misma postura de privacidad (no se guardan los
  argumentos). Cada herramienta MCP declara la acción de rol que exige; sin
  entrada en esa tabla, se deniega — no hay "permitida por omisión".

**Lo que NO quedó hecho de este paso:** la **redacción de datos sensibles**
dentro del prompt (punto 5.4). Detectar qué fragmento de una nota es un dato
personal exige un clasificador; un redactor a base de expresiones regulares
daría una falsa sensación de seguridad, que es peor que no tenerlo. La
protección real que sí está es de grano más grueso pero honesta: el namespace
entero no sale. Tampoco hay **retención/rotación** del registro de auditoría:
crece sin límite y se borra a mano.

---

## Paso 6 — Expansión (solo después de 1-5)

Una vez el circuito principal es correcto, confiable y está cubierto por
tests, recién entonces tiene sentido:

- Implementar adaptadores para `openai`, `google`, `mistral`, `openrouter`
  (o eliminarlos de `models.yaml` si no se van a mantener, para no prometer
  un fallback que no existe).
- Planner real (hoy es diseño en `docs/`, no código).
- Memoria persistente integrada al motor (`orchestration/memory/` ya tiene
  esqueletos — `memory_engine.py`, `sqlite_memory_engine.py` — pendientes
  de conectar al flujo de `ask()`).
- Bus de eventos, observabilidad, escalabilidad a cientos de agentes (temas
  ya cubiertos en `docs/04-MAGNUS-V2-ARQUITECTURA.md` a nivel de diseño).
- **Cancelación cooperativa** de una consulta en vuelo (deuda declarada del
  paso 2): exige un modelo de ejecución asíncrono o con hilos que hoy no
  existe. Los timeouts por adaptador acotan el daño mientras tanto.
- **Evaluador LLM-as-judge** contra `evaluation.rubric_ref` (deuda declarada
  del paso 3): el puerto `Evaluator` está listo; falta la implementación, que
  ya tiene coste y latencia asociados.

**No empezar el resto de esta lista sin terminar 1-5.** Añadir superficie
nueva sobre una base con el motor LLM roto, sin tests y sin evaluación
obligatoria multiplica el costo de arreglar los pasos 1-5 más adelante. El
primer bloque de abajo (enrutado semántico) sí se hizo porque los pasos 1-5
ya estaban cerrados y verificados de punta a punta.

### Bloque 1 de este paso — enrutado semántico local y seguro ✅ COMPLETADO (2026-07-25)

**Objetivo:** mejorar la cobertura de consultas coloquiales/sinónimos sin
aumentar rutas incorrectas, con un `EmbeddingCapabilityMatcher` local,
explicable y seguro — nunca embeddings neuronales, nunca un fallback que
adivine.

**Medido antes de diseñar la combinación (no supuesto):** se corrió el
coseno de `HashingEmbedder` (el mismo del RAG) contra las 17 capacidades
reales con ~25 consultas genuinas, coloquiales y sin relación, ANTES de
escribir el combinador híbrido. Resultado honesto: **el coseno puro es más
débil que el léxico en todos los casos probados**, y para varias consultas
con vocabulario real apuntaba a la capacidad **incorrecta** con más
confianza que a la correcta —p. ej. "quiero mejorar mi alimentación diaria"
puntuaba `project_management` (0.162) por encima de `nutrition`, que ni
siquiera aparecía en el top-2, mientras el léxico acertaba con 0.609—. El
ruido de fondo entre una consulta sin relación y una capacidad cualquiera
llega hasta 0.187 ("qué es un smartphone plegable" → `mental_health` 0.187).
Dejar que el coseno decidiera solo habría **aumentado** las rutas
incorrectas, justo lo que este bloque prohíbe.

**Diseño resultante, condicionado por esa medición:**
- [`EmbeddingCapabilityMatcher`](orchestration/capability/matcher.py) —
  implementación independiente del `CapabilityMatcher` (mismo protocolo que
  `LexicalCapabilityMatcher`, sin tocarlo). Dos señales: coseno (ruidoso,
  con `NOISE_FLOOR=0.30` calibrado con margen de 0.11 sobre el ruido medido)
  y **sinónimo exacto** declarado en `capabilities/*.yaml` (determinista,
  `via="synonym"`, confianza máxima) — la señal que sí demostró ser segura.
- [`HybridCapabilityMatcher`](orchestration/capability/matcher.py) —
  combinador con política explícita, no un promedio de dos escalas: el
  léxico manda si ya encuentra algo; el sinónimo exacto es la única vía por
  la que el canal vectorial puede incluir una capacidad *por sí solo*; el
  coseno puro solo **refuerza** un match léxico existente (`REINFORCEMENT_WEIGHT=0.10`)
  o incluye algo por sí solo si cruza su propio umbral alto (0.30, medido).
  Sin capacidad, ningún canal por debajo de su propio umbral cuela nada —
  el "sin dominio" del motor (`MagnusEngine._sin_dominio`, corrección
  previa) sigue intacto y sin tocar.
- Vías auditables devueltas: `synonym`, `lexical`, `embedding`, `hybrid`
  (ambos canales de acuerdo), `parent`/`related` (propagación de taxonomía,
  en cualquiera de los dos canales).
- `CapabilityEngine.explain(query, agent_id)` expone ahora, por capacidad
  candidata, el score léxico, el vectorial, el final, el motivo de selección
  y el umbral aplicado (`HybridChannelScores` vía `explain_detailed`),
  incluso cuando NINGÚN canal pasó — para poder auditar por qué algo no se
  enrutó, no solo qué sí.
- **`CapabilityEngine` usa `HybridCapabilityMatcher` por defecto** desde
  ahora (antes: `LexicalCapabilityMatcher` a secas) — se cambió el default
  solo después de medir que no empeora nada (ver benchmark abajo). Pasar
  `matcher=LexicalCapabilityMatcher(catalog)` explícito recupera el
  comportamiento anterior.

**Tres sinónimos añadidos a capacidades reales** (permitido explícitamente:
"añade sinónimos solo si son necesarios para casos golden demostrables"),
cada uno porque la consulta real NO encontraba ningún agente sin él:
`plata` → `finance` (`capabilities/finance.capability.yaml`), `chamba` →
`career_transition`, `pegar el ojo` → `fitness`. Se **descartó** un cuarto
candidato (`quemado` → `mental_health`, para el caso "ando bien quemado con
tanto trabajo") tras medir que producía solo un empate ambiguo con
`project_management` (amanda 1.011 vs. serena 1.0) por la propagación vía
`related` hacia `career_transition` — no una mejora limpia, así que no se
añadió.

**Banco de enrutado** en [`evaluation/bench_routing.py`](evaluation/bench_routing.py)
con 26 consultas reales/coloquiales en [`evaluation/goldens/routing.yaml`](evaluation/goldens/routing.yaml)
(finanzas, salud/sueño, productividad/carrera, tecnología, ambiguas, sin
dominio). Resultado exacto (`python -m evaluation.bench_routing`):

| Métrica | Léxico solo (baseline) | Híbrido (ahora, default) |
|---|---|---|
| Cobertura | 100.0% | 100.0% |
| Precisión | 100.0% | 100.0% |
| Falsos positivos | 0/4 | 0/4 |

**Honestidad sobre lo que esto SÍ mide y lo que NO:** el híbrido empata con
el léxico en este set — no lo supera. La ganancia de cobertura medida (3
consultas que antes no enrutaban a nadie) viene de los **sinónimos añadidos
al catálogo**, que benefician a los dos matchers por igual, porque
`LexicalCapabilityMatcher` también indexa `synonyms`. El valor propio y
diferenciado del canal vectorial, demostrado con un test dedicado
(`test_la_diluvion_lexica_se_corrige_por_sinonimo_exacto`), es distinto:
para la consulta "tengo ardor de estómago después de comer", el léxico solo
puntúa más alto a `nutrition` (0.617) que a `gastroenterology` (0.54) porque
la señal del sinónimo "estómago" se diluye entre más palabras que matchean
`nutrition` — el canal de sinónimo exacto no se diluye (es booleano) y
corrige la capacidad top-1 a `gastroenterology`. En el roster de agentes
actual esto **no cambia el agente final** (`gastroenterology` es huérfana a
propósito, sin agente asignado; `medicine`, que sí tiene agente, termina
recibiendo el mismo dr_soma por ambos caminos) — es una mejora real de
corrección/explicabilidad a nivel de capacidad, documentada así, no
presentada como un salto de precisión a nivel de agente que el benchmark no
midió.

**Lo que NO quedó hecho de este bloque:** la mayoría de las paráfrasis
coloquiales genuinas (verificado: "necesito ser más eficiente con mi
tiempo", coseno 0.099; "estoy agotado emocionalmente por el trabajo", coseno
0.158 y encima apuntando a la capacidad incorrecta) siguen sin resolverse
por el canal vectorial — es una limitación real del corpus (17 capacidades,
textos de 20-40 palabras, insuficiente para que el coseno separe señal de
ruido con margen amplio) y de la técnica (random indexing, no semántica
real), no algo oculto. Resolverlo de verdad exigiría un embedder neuronal —
explícitamente fuera de alcance de este bloque. Un reranker cross-encoder
para el canal vectorial tampoco se implementó, por la misma razón que en
el paso 4 (RAG): exige un modelo.

**Riesgo/deuda que queda:** se descubrió (no se corrigió, por estar fuera de
alcance) que `LexicalCapabilityMatcher._STOP` no filtra demostrativos
españoles comunes ("este", "esta", "eso"…): la consulta "no sé en qué gastar
mi plata este mes" matcheaba `programming` con score 0.558 por la palabra
suelta "este" (presente en el routing_example "cómo estructuro **este**
código"), antes de que "plata" como sinónimo la corrigiera a `finance`. No
se tocó el `_STOP` de `LexicalCapabilityMatcher` en este bloque —cambiar su
comportamiento no estaba en alcance—, pero es un candidato claro de
limpieza de bajo riesgo para una próxima pasada por este mismo archivo.

**Cómo verificarlo:** `python -m pytest tests/test_capability_matching.py`
(22 tests) + `python -m evaluation.bench_routing`.

---

## Tabla de estado por componente

| Componente | Diseñado (docs/) | Implementado (código) | Conectado al runtime real |
|---|---|---|---|
| Agent Registry + validación + herencia | ✅ | ✅ | ✅ |
| Capability Engine (enrutado léxico + taxonomía) | ✅ | ✅ | ✅ |
| Enrutado seguro (sin dominio → se declara, no se adivina) | ✅ | ✅ | ✅ |
| Enrutado semántico local (`EmbeddingCapabilityMatcher` + `HybridCapabilityMatcher`) | ✅ | ✅ | ✅ (default de `CapabilityEngine`) |
| Enrutado por embeddings neuronales | ✅ (diseño) | ❌ | ❌ (puerto `CapabilityMatcher` listo) |
| RAG — ingesta + recuperación léxica | ✅ | ✅ | ✅ |
| RAG — recuperación vectorial local (random indexing + TF-IDF) | ✅ | ✅ | ✅ |
| RAG — embeddings neuronales (bge-m3) | ✅ | ❌ | ❌ (puerto `Embedder` listo) |
| RAG — reranker cross-encoder | ✅ | ❌ | ❌ (contrato `Reranker` listo) |
| `ProviderRegistry` (perfil→provider+fallback) | ✅ | ✅ | ✅ (inyectado en `MagnusEngine`) |
| Adaptador Anthropic | ✅ | ✅ | ✅ |
| Adaptador Ollama | ✅ | ✅ | ✅ |
| Adaptadores OpenAI/Google/Mistral/OpenRouter | ✅ (declarados) | ❌ | ❌ (ningún perfil los referencia) |
| Evaluador de respuestas — estructural (citas) | ✅ | ✅ | ✅ |
| Evaluador de respuestas — LLM-as-judge (`rubric_ref`) | ✅ (diseño) | ❌ | ❌ (puerto listo) |
| Verificación de citas obligatoria | ✅ (campo en cada agente) | ✅ | ✅ (activada en `engine.py`) |
| Guardrails por dominio + escalado por urgencia | ✅ | ✅ (`configs/guardrails.yaml`) | ✅ |
| Citas reproducibles (hash + snapshot de la wiki) | ✅ | ✅ | ✅ |
| Enforcement de permisos | ✅ (`permissions.yaml`) | ✅ (`orchestration/permissions.py`) | ✅ |
| Auditoría de consultas y de herramientas | ✅ | ✅ (`orchestration/audit.py`, opt-in) | ✅ |
| Privacidad / egress de datos de la wiki | ✅ (`configs/privacy.yaml`) | ✅ | ✅ |
| Redacción de datos sensibles en el prompt | ⚠️ | ❌ | ❌ (exige clasificador) |
| Auth/CORS/rate-limit en MCP HTTP | ✅ | ✅ (`mcp_server/http_guard.py`) | ✅ |
| Retención/rotación del registro de auditoría | ⚠️ | ❌ | ❌ |
| Resiliencia operativa (timeouts, reintentos, límite de contexto) | ✅ | ✅ | ✅ |
| Cancelación cooperativa de consultas en vuelo | ⚠️ | ❌ | ❌ (motor síncrono) |
| Planner | ✅ (diseño) | ❌ | ❌ |
| Memoria persistente | ✅ (diseño + esqueletos) | ⚠️ (parcial) | ❌ |
| Empaquetado / dependencias declaradas | — | ✅ (`pyproject.toml`) | — |
| Tests automatizados | — | ✅ (`tests/`, `pytest`) | ✅ (cubren el motor real) |

---

## Cómo usar este documento

Cada paso (1-5) debe poder cerrarse de forma independiente y verificable
antes de pasar al siguiente. Al completar un paso, actualizar la tabla de
estado de componentes de arriba en el mismo commit — así el roadmap se
mantiene honesto en vez de convertirse en aspiracional otra vez.
