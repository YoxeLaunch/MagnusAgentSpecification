# Camino de Desarrollo y Registro de Modificaciones — Magnus

Este documento (**`CAMINO.md`**) sirve como la fuente de verdad y registro continuo (*log*) para todas las propuestas, análisis, revisiones de arquitectura, modificaciones y adiciones en la plataforma **Magnus Dynamic Group**.

---

## 📌 Estado del Sistema (2026-07-29)

* **Versión del Proyecto**: `0.2.0`
* **Suite de Pruebas**: 203/203 pruebas aprobadas (100% de éxito).
* **Desempeño RAG**: 94.7% de recall@8 sobre el banco de 19 casos golden (Recuperación Híbrida: Léxica + Random Indexing).
* **Desempeño de Enrutado**: 100% precisión en el dataset golden (26 casos).

> ⚠️ **Limitación de arquitectura conocida**: el namespace de un chunk en el
> RAG es solo el primer segmento de ruta bajo `wiki/` (`kernel/rag/file_store.py:87`).
> No existe granularidad de subcarpeta en la recuperación. Cualquier intento
> de acotar `permissions.knowledge.read` por debajo de ese nivel es un error
> de configuración detectable — ver Fase 1.4 y Fase 3.3 abajo.

---

## 🗺️ Planteamiento de Correcciones, Mejoras y Nuevas Funcionalidades

### 🔹 Fase 1: Correcciones Inmediatas y Cobertura de Tests (Corto Plazo)
- [x] **1.1 Unificación de `_STOP` y Filtrado de Demostrativos en Enrutado Léxico**
  - **Ubicación**: [`kernel/rag/embedder.py`](file:///c:/MagnusAgent/kernel/rag/embedder.py) y [`orchestration/capability/matcher.py`](file:///c:/MagnusAgent/orchestration/capability/matcher.py)
  - **Cambio**: Se unificó `_STOP` como fuente única de verdad en `embedder.py` importándose en `matcher.py`, añadiendo demostrativos españoles (*"este"*, *"esta"*, *"ese"*, *"esa"*, *"esto"*, *"eso"*, *"aquel"*, *"aquella"*).
- [x] **1.2 Limpieza de Excepciones Redundantes en `agent_registry.py`**
  - **Ubicación**: [`orchestration/registry/agent_registry.py`](file:///c:/MagnusAgent/orchestration/registry/agent_registry.py#L330)
  - **Cambio**: Se simplificó `except (InheritanceError, Exception)` a `except Exception` en `validate_as()`.
- [x] **1.3 Resolución de Seguridad en Permisos de Namespace (revisada)**
  - **Ubicación**: [`orchestration/permissions.py`](file:///c:/MagnusAgent/orchestration/permissions.py) y [`tests/test_permissions.py`](file:///c:/MagnusAgent/tests/test_permissions.py)
  - **Problema Detectado**: Si la política declaraba una subcarpeta acotada (ej. `01-Finanzas/personal`) y el agente solicitaba la carpeta ancha (`01-Finanzas`), `_coincide` devolvía `True`, concediendo la carpeta entera en RAG y provocando una fuga de subcarpetas no autorizadas.
  - **Primer intento (superado)**: se ajustó `_coincide()` (correcto y se mantiene) y se intentó que `allowed_namespaces()` **acotara automáticamente la solicitud a la subcarpeta permitida** — pero se detectó que el RAG (`kernel/rag/file_store.py:87`) solo indexa namespaces de primer nivel bajo `wiki/`; no existe granularidad de subcarpeta en la recuperación real. Ese "acotamiento" devolvía un namespace que ningún chunk real matchea nunca — no era mínimo privilegio, era una consulta que siempre recupera cero chunks disfrazada de éxito.
  - **Solución final**: `allowed_namespaces()` ahora lanza `NamespaceGranularityError` cuando detecta este caso — falla ruidosamente en vez de degradar en silencio. `MagnusEngine.__init__` valida a todos los agentes activos contra esto al arrancar (`_verificar_granularidad_de_namespaces`), y `ask()` lo captura como defensa en profundidad para agentes recargados en caliente (`_denegado_por_configuracion`). Test actualizado en `test_permissions.py` para verificar que se lanza la excepción, no que se acota silenciosamente. 184/184 tests.
- [x] **1.4 Suite de Pruebas para `SqliteMemoryEngine` y `scaffold.py`**
  - **Ubicación**: [`orchestration/memory/sqlite_memory_engine.py`](file:///c:/MagnusAgent/orchestration/memory/sqlite_memory_engine.py), [`sdk/scaffold.py`](file:///c:/MagnusAgent/sdk/scaffold.py), [`tests/test_scaffold.py`](file:///c:/MagnusAgent/tests/test_scaffold.py)
  - **Estado**: `tests/test_memory.py` creado (ver Fase 2.1) — cubre `SqliteMemoryEngine` y detectó un bug real (ver abajo). `tests/test_scaffold.py` (17 casos) cierra el residual: valida que `agent_yaml()` produce YAML parseable con `status: draft`, capacidades/sources/extends/fallback_profile correctos; que `markdown_files()` genera exactamente el conjunto de `REQUIRED_MD_FILES` de `agent_registry.py` (importado directo, no duplicado, para no divergir de la fuente de verdad) con nombre/rol interpolados y los placeholders `<!-- completa -->` que bloquean `status: active` a propósito; y que `capability_yaml()` serializa `parent`/`routing_examples` correctamente. Fase 1 100% cerrada.

---

### 🔹 Fase 2: Conexión de Memoria y Paralelización de Consultas (Medio Plazo)
- [x] **2.1 Conectar `MemoryEngine` como Middleware Inyectable**
  - **Ubicación**: [`orchestration/engine.py`](file:///c:/MagnusAgent/orchestration/engine.py), [`orchestration/memory/memory_engine.py`](file:///c:/MagnusAgent/orchestration/memory/memory_engine.py), [`tests/test_memory.py`](file:///c:/MagnusAgent/tests/test_memory.py)
  - **Cambio**: `MagnusEngine.__init__` acepta `memory: MemoryEngine | None` (por defecto `NullMemoryEngine`, mismo patrón opt-in que `trace_store`). `ask(message, agent_id=None, *, user_id="anonimo", session_id=None)` recupera memoria de sesión (`short_term`, últimos turnos sin filtro de texto) y de usuario (`long_term`, filtrada por el mensaje) ANTES de construir el prompt del LLM, y graba el turno DESPUÉS de responder — nunca dentro de `RAGRequest`. La memoria se adjunta al system prompt como bloque separado, explícitamente marcado "NO es evidencia de tu wiki".
  - **Bug real encontrado y corregido**: `SqliteMemoryEngine.recall()` para `short_term` solo filtraba por `session_id`, ignorando `agent_id`/`user_id` — dos agentes consultados en la misma sesión veían la memoria uno del otro (fuga de contexto entre agentes). Corregido en `sqlite_memory_engine.py`; test de regresión: `test_memoria_es_por_agente_no_se_filtra_entre_agentes`.
  - **⚠️ Aviso de privacidad conocido, sin cerrar**: los ítems de memoria no llevan namespace y no pasan por `EgressPolicy` como los chunks de la wiki. Hoy solo se atenúa porque se adjuntan usando el mismo veredicto de egreso que los chunks de la consulta EN CURSO, no según la sensibilidad del contenido de la memoria en sí. Cerrarlo de raíz requiere etiquetar `MemoryItem`/`MemoryScope` con sensibilidad — queda como ítem futuro, no bloqueante para esta fase.
- [x] **2.2 Paralelización de Llamadas a Proveedor cuando `len(agents) > 1`**
  - **Ubicación**: [`orchestration/engine.py`](file:///c:/MagnusAgent/orchestration/engine.py), [`tests/test_engine_concurrency.py`](file:///c:/MagnusAgent/tests/test_engine_concurrency.py)
  - **Cambio**: `_agent_answer` se partió en `_preparar_respuesta` (permisos, guardrails, recuperación, egreso — secuencial, sin red) y `_resolver_pendiente` (la llamada al proveedor — la única parte que hace red). Con 0 o 1 agente pendiente se resuelve igual que antes, sin crear hilos; con más de uno, se despachan con `ThreadPoolExecutor` (`max_provider_workers`, por defecto 4) y los resultados se reordenan según el orden original de enrutado. `trace_store.record()` se protegió con un lock (`self._trace_lock`) para que dos hilos no intercalen escrituras en el mismo archivo JSONL.
- [x] **2.3 (derivado de la auditoría) Test de Integración RAG↔Permisos**
  - **Ubicación**: [`tests/test_rag_permissions_integration.py`](file:///c:/MagnusAgent/tests/test_rag_permissions_integration.py)
  - **Motivación**: el bug de la Fase 1.3 (namespace de subcarpeta que el RAG nunca indexa) solo era detectable cruzando las dos capas — ningún test unitario de `permissions.py` por sí solo podía verlo. Estos tests fijan el invariante ("namespaces siempre de primer nivel") contra la wiki de test, la wiki real del repo, y verifican que `MagnusEngine.__init__` falla al arrancar (no en la primera consulta) si una política declara una subcarpeta.

---

### 🔹 Fase 3: Adaptadores de Proveedor y Rotación de Traza (Medio-Largo Plazo)
- [x] **3.1 Implementar Adaptadores Pendientes (`GoogleProvider` y `OpenAIProvider`)**
  - **Ubicación**: [`providers/openai_provider.py`](file:///c:/MagnusAgent/providers/openai_provider.py), [`providers/google_provider.py`](file:///c:/MagnusAgent/providers/google_provider.py), [`mcp_server/protocol.py`](file:///c:/MagnusAgent/mcp_server/protocol.py), [`configs/models.yaml`](file:///c:/MagnusAgent/configs/models.yaml)
  - **Cambio**: Adaptadores livianos bajo `LLMProvider`, con cliente inyectable (mismo patrón que `AnthropicProvider`) para testear sin red ni credenciales. `OpenAIProvider` traduce `effort` → `reasoning_effort` y parsea `tool_calls` desde JSON serializado; descarta `thinking` (sin equivalente) en vez de volcarlo crudo. `GoogleProvider` mueve el system prompt a `system_instruction`, traduce roles `user`/`model`, y aproxima tanto `thinking` como `effort` al único knob que Gemini tiene (`thinking_config.thinking_budget`). `_build_provider_registry()` en `mcp_server/protocol.py` ahora reconoce `MAGNUS_PROVIDER=openai|google` (y los suma en `auto`), con el mismo patrón de "omitido si falta la API key / el paquete" que `anthropic`.
  - **Alcance deliberadamente NO incluido**: ningún perfil de `models.yaml` referencia `openai`/`google` todavía — solo se declaró el adaptador (`api_key_env`, sin `adapter: pendiente`). Incorporarlos a una cadena de fallback es una decisión de producto (qué perfil degrada a qué proveedor) separada de tener el adaptador disponible.
  - **Tests**: `tests/test_openai_adapter.py` (13 casos) y `tests/test_google_adapter.py` (13 casos), mismo patrón que `test_anthropic_adapter.py` — cliente stub inyectado, sin red. 234/234 tests.
- [ ] **3.2 Rotación de Trazas JSONL por Tamaño Máximo (`MAGNUS_TRACE_MAX_MB`)**
  - **Ubicación**: [`orchestration/audit.py`](file:///c:/MagnusAgent/orchestration/audit.py)
  - **Solución**: Implementar una purga perezosa de archivos `.jsonl` antiguos en `JsonlTraceStore.record()` basada en un límite de tamaño total de directorio.
- [ ] **3.3 Granularidad de Subcarpeta en el Namespace del RAG**
  - **Ubicación**: [`kernel/rag/file_store.py`](file:///c:/MagnusAgent/kernel/rag/file_store.py) y [`kernel/rag/vector_store.py`](file:///c:/MagnusAgent/kernel/rag/vector_store.py)
  - **Problema**: Hoy el namespace de un chunk es solo el primer segmento de ruta bajo `wiki/` (`file_store.py:87`). Una política que necesite acotar por debajo de ese nivel (ej. `01-Finanzas/personal`) no puede expresarse — `permissions.py` la rechaza explícitamente con `NamespaceGranularityError` (ver 1.3).
  - **Solución**: extender `_index_document` para indexar la ruta relativa completa (o un campo `subpath` adicional) y actualizar `_ns_match` en ambos retrievers para comparar a ese nivel.
  - **⚠️ Dependencia**: al implementar esto, **retirar o relajar** el `NamespaceGranularityError` de `allowed_namespaces()` en `orchestration/permissions.py` — ese guard existe precisamente porque hoy esta granularidad no existe; dejarlo intacto bloquearía la funcionalidad nueva.

---

### 🔹 Fase 4: Evaluaciones Avanzadas y Reranker (Largo Plazo)
- [ ] **4.1 Evaluador Semántico (`LLM-as-judge`)**
  - **Ubicación**: [`orchestration/evaluation/`](file:///c:/MagnusAgent/orchestration/evaluation/)
  - **Solución**: Implementar la evaluación contra la rúbrica del agente (`rubric_ref`) utilizando un modelo secundario.
- [ ] **4.2 Modelo de Cancelación Cooperativa**
  - **Ubicación**: [`orchestration/engine.py`](file:///c:/MagnusAgent/orchestration/engine.py)
  - **Solución**: Introducir un modelo de ejecución asíncrona para permitir la cancelación de peticiones en vuelo.

---

## 📜 Historial de Registros y Modificaciones

| Fecha | Tipo | Descripción | Estado |
| :--- | :---: | :--- | :---: |
| **2026-07-29** | **AUDITORÍA** | Realización de la auditoría técnica global. Verificación de 183 tests pasados y benchmarks. | ✅ Completado |
| **2026-07-29** | **DOCS** | Creación de [`CAMINO.md`](file:///c:/MagnusAgent/CAMINO.md) para llevar el registro formal de decisiones y roadmap. | ✅ Completado |
| **2026-07-29** | **REVIEW** | Revisión por el Ingeniero Principal / Master. Validación de la arquitectura RRF, permisos y estrategia de memoria/paralelización. | ✅ Completado |
| **2026-07-29** | **FIX** | Unificación de `_STOP` con demostrativos en `embedder.py`/`matcher.py`, limpieza de excepción en `agent_registry.py`, aclaración de `check_tool` en `permissions.py`. | ✅ Completado |
| **2026-07-29** | **SECURITY** | Corrección de la semántica de permisos en `permissions.py`: `_coincide()` deja de conceder la carpeta ancha cuando la política es más específica. Tests de permisos 100% pasando (184/184). | ✅ Completado |
| **2026-07-29** | **CORRECCIÓN** | Se detectó que el "acotamiento automático" del fix anterior era inviable: el RAG no indexa subcarpetas, así que la subcarpeta devuelta por `allowed_namespaces()` nunca matchea ningún chunk real (fallo silencioso, no acotamiento). Reemplazado por `NamespaceGranularityError` explícita, validada al construir `MagnusEngine` y capturada en `ask()`. Granularidad real de subcarpeta en el RAG queda pendiente (Fase 3.3), con nota de dependencia sobre este guard. 184/184 tests. | ✅ Completado |
| **2026-07-29** | **FEATURE** | Fase 2 completa: memoria conversacional conectada como middleware inyectable en `MagnusEngine.ask()` (2.1), paralelización de llamadas a proveedor con `ThreadPoolExecutor` cuando hay más de un agente (2.2), y suite de integración RAG↔Permisos (2.3). Se encontró y corrigió un bug real de fuga de memoria entre agentes en `SqliteMemoryEngine.recall()`. 203/203 tests. Aviso de privacidad de memoria sin egreso queda documentado y pendiente. | ✅ Completado |
| **2026-07-29** | **FEATURE** | Fase 3.1: adaptadores `OpenAIProvider` y `GoogleProvider` bajo `LLMProvider`, cableados en `mcp_server/protocol.py` (`MAGNUS_PROVIDER=openai\|google\|auto`) y declarados en `configs/models.yaml` sin `adapter: pendiente`. Ningún perfil los referencia todavía (decisión de producto aparte). 234/234 tests. | ✅ Completado |
| **2026-07-29** | **TESTS** | Fase 1.4 residual cerrada: `tests/test_scaffold.py` (17 casos) para `sdk/scaffold.py`, verificando contra `REQUIRED_MD_FILES` de `agent_registry.py` para no divergir de la fuente de verdad. Fase 1 100% completa. 250/250 tests. | ✅ Completado |
