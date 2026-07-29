# 🚀 Magnus Agent Engine (MAS)

**Sistema operativo de agentes inteligentes soberano, independiente del proveedor de IA y centrado en privacidad (Local-First).**

> **Autoría & Desarrollo:** Creado y mantenido por **Yoxe / Magnus Dynamic Group** ([`YoxeLaunch/MagnusAgentSpecification`](https://github.com/YoxeLaunch/MagnusAgentSpecification)).
> 
> Los agentes en Magnus **no** almacenan conocimiento estático ni alucinan datos privados: consultan una base documental versionada (**LLM-Wiki**) mediante un pipeline RAG Híbrido y respetan el estándar **MAS** (Magnus Agent Specification). El motor es agnóstico del proveedor de LLM e incluye adaptadores nativos para **Anthropic**, **OpenAI**, **Google Gemini** y **Ollama** (ejecución 100% local).

---

## 🎯 ¿Qué es Magnus Agent Engine?

**Magnus** es un entorno runtime y sistema operativo multiagente diseñado para resolver el dilema entre inteligencia artificial avanzada y privacidad/soberanía de los datos.

En un entorno tradicional, enviar datos financieros, de salud o personales a modelos de lenguaje en la nube implica riesgos de privacidad, falta de gobernanza y alucinaciones. Magnus resuelve esto separando el **Razonamiento** (modelos de IA) del **Conocimiento** (archivos Markdown estructurados en `LLM-Wiki/`), imponiendo un control de acceso riguroso en tiempo de ejecución:

* **Conocimiento Soberano (Local-First):** Toda la información corporativa o personal reside en Markdown versionado en local.
* **Seguridad y Privacidad Estricta:** Los namespaces marcados como `local_only` (ej. salud mental, finanzas personales) jamás abandonan el dispositivo hacia APIs externas.
* **Integración MCP Nativa (Model Context Protocol):** Magnus opera como un servidor MCP listo para conectarse a clientes como Claude Code, Cursor, VSCode o agentes autónomos vía `stdio` o `HTTP`.
* **Multi-Proveedor de IA:** Soporte dinámico para **Anthropic Claude**, **OpenAI (GPT-4o)**, **Google Gemini** y **Ollama**, con caída automática a respuestas extractivas si se carece de API Keys o conexión.

---

## ✨ Características Principales

| Característica | Descripción |
|---|---|
| 🤖 **Orquestación Multiagente (MAS)** | Arquitectura extensible basada en `agent.yaml`, con taxonomía de capacidades, reglas éticas, guardrails y contexto aislado. |
| 🧠 **RAG Híbrido Calibrado (94.7% Recall)** | Recuperación combinada **Léxica (TF-IDF)** + **Vectorial Local (Random Indexing / Coseno)** con Reciprocal Rank Fusion (RRF) sobre pasajes reales. |
| 🎯 **Enrutado de Capacidades (Capability Matching)** | Clasificación determinista de intención combinando solape léxico, sinónimos coloquiales curados y filtrado de taxonomía de ancestros. |
| 🔒 **Engine de Privacidad y Permisos Granulares** | Enforce en tiempo de ejecución de `privacy.yaml` (bloqueo de egreso remoto) y `permissions.yaml` (acceso a herramientas/namespaces por agente). |
| ⚡ **Servidor MCP Integrado** | Soporte completo del protocolo MCP tanto en transporte de bajo nivel `stdio` (sin puertos expuestos) como servidor HTTP seguro con tokens Bearer y CORS. |
| 📊 **Auditoría & Trazabilidad Completa** | Trazas estructuradas JSONL auditables con motivos de enrutado, puntuaciones RAG y estado de guardrails. |

---

## 🔌 Proveedores de IA Soportados

El puerto `LLMProvider` ([`providers/base.py`](providers/base.py)) abstrae la comunicación con los modelos de lenguaje. Se seleccionan de forma transparente según el perfil del agente en `configs/models.yaml`:

* **Ollama ([`providers/ollama_provider.py`](providers/ollama_provider.py)):** Ejecución 100% local, privado, sin coste y sin conexión a internet.
* **Anthropic ([`providers/anthropic_provider.py`](providers/anthropic_provider.py)):** Modelos Claude (Claude 3.5 Sonnet, Claude 3 Opus, etc.).
* **OpenAI ([`providers/openai_provider.py`](providers/openai_provider.py)):** Modelos GPT-4o, GPT-4o-mini y embeddings compatibles.
* **Google Gemini ([`providers/google_provider.py`](providers/google_provider.py)):** Modelos Gemini 1.5 Pro y Gemini 1.5 Flash.
* **Modo Extractivo (Sin LLM):** Si no hay API Key o proveedor activo, Magnus responde citando pasajes literales de la wiki sin costo ni llamadas externas.

---

## 📐 Estructura del Repositorio y Arquitectura

```
MAGNUS/
├── LLM-Wiki/wiki/   # Base documental versionada (fuente de verdad del conocimiento)
├── agents/          # Agentes definidos bajo el estándar MAS (ernesto_libras, dr_soma, etc.)
├── capabilities/    # Catálogo de capacidades y taxonomía de enrutado
├── constitution/    # Constitución ética, guardrails, evidencias y citación
├── orchestration/   # Motor de orquestación, router, permisos, memoria SQLite y auditoría
├── providers/       # Adaptadores de IA (Anthropic, OpenAI, Google, Ollama, Registry)
├── kernel/rag/      # Ingesta de archivos, retriever léxico, vectorial y pipeline híbrido
├── mcp_server/      # Servidor MCP (transportes stdio y HTTP) + controles de acceso
├── evaluation/      # Benchmarks de recuperación RAG y enrutado de capacidades
├── configs/         # models.yaml, permissions.yaml, privacy.yaml, guardrails.yaml
├── tests/           # Suite de verificación completa (250+ tests en pytest)
└── docs/            # Especificación formal MAS y documentos de arquitectura
```

### Documentación de Diseño Formal

| Documento | Contenido |
|-----------|-----------|
| [`docs/00-VISION-Y-ARQUITECTURA.md`](docs/00-VISION-Y-ARQUITECTURA.md) | Tesis, principios, Clean Architecture, DDD, Hexagonal, flujo multiagente y escalabilidad. |
| [`docs/01-MAS-especificacion.md`](docs/01-MAS-especificacion.md) | Especificación del estándar MAS: estructura de un agente, `agent.yaml`, validación y herencia. |
| [`docs/02-COMPONENTES.md`](docs/02-COMPONENTES.md) | Los 15 componentes del sistema con sus interfaces, flujos y tecnologías. |
| [`docs/04-MAGNUS-V2-ARQUITECTURA.md`](docs/04-MAGNUS-V2-ARQUITECTURA.md) | **Normativo.** Reconciliación runtime↔docs: Agent Registry, Capability Engine, herencia y SDK. |

---

## 🔬 Especificaciones Técnicas

### 1. Sistema RAG Híbrido (Retrieval-Augmented Generation)

El sistema RAG en Magnus combina dos métodos complementarios sobre los mismos chunks de texto:

| Retriever | Arquitectura / Algoritmo | Propósito |
|---|---|---|
| **Léxico** | [`kernel/rag/file_store.py`](kernel/rag/file_store.py) | Ponderación Term Frequency (TF) sobre tokens normalizados. Excelente para términos exactos y códigos. |
| **Vectorial Local** | [`kernel/rag/embedder.py`](kernel/rag/embedder.py) + [`vector_store.py`](kernel/rag/vector_store.py) | **Random Indexing con pesos TF-IDF** sobre unigramas, bigramas y prefijos en español, evaluado por similitud de Coseno. No requiere dependencias pesadas (Torch/Transformers). |

Los resultados se combinan mediante **Reciprocal Rank Fusion (RRF)** para determinar el orden de relevancia final, aplicando los umbrales específicos de cada agente (`min_score`).

> **Rendimiento medido sobre el corpus real (`python -m evaluation.bench_retrieval`):**
> * Solo Léxico: **89.5%** Recall@8
> * Solo Vectorial: **73.7%** Recall@8
> * **RAG Híbrido RRF: 94.7% Recall@8** 🚀

### 2. Enrutado de Capacidades (Capability Matching Engine)

El `CapabilityEngine` identifica qué agente debe atender una consulta sin necesidad de llamadas costosas a LLMs para clasificación:

* **`LexicalCapabilityMatcher`**: Ponderación por solape IDF sobre `description`, `routing_examples` y `synonyms`, propagada por la jerarquía taxonómica de la capacidad.
* **`EmbeddingCapabilityMatcher`**: Coseno vectorial sobre `HashingEmbedder` + canal de **sinónimo exacto** determinista.
* **Explicabilidad (`CapabilityEngine.explain`)**: Devuelve el desglose detallado de puntuaciones (léxico vs vectorial), el motivo de enrutado (`via: synonym/lexical/hybrid/parent`) y la traza completa de auditoría.

---

## 💻 Instalación, Verificación y Ejecución

### Requisitos Previos
* **Python 3.10** o superior.

### 1. Instalación del Entorno
```bash
# Clonar repositorio
git clone https://github.com/YoxeLaunch/MagnusAgentSpecification.git
cd MagnusAgentSpecification

# Instalación en modo desarrollo
python -m pip install -e ".[dev]"
```

### 2. Ejecutar la Suite de Pruebas
Magnus cuenta con una suite de verificación con más de 250 pruebas unitarias e integrales que se ejecutan localmente sin necesidad de claves API ni internet:

```bash
python -m pytest
```

### 3. Ejecutar Benchmarks de Evaluación
```bash
# Benchmarking de precisión RAG
python -m evaluation.bench_retrieval

# Benchmarking de enrutado de capacidades
python -m evaluation.bench_routing
```

---

## 🔌 Conectar Magnus vía MCP (Model Context Protocol)

Magnus actúa como un servidor de agentes que expone herramientas estándar MCP (`magnus_ask` y `magnus_list_agents`).

### Modo 1: Transporte `stdio` (Recomendado para Claude Code, Cursor, Codex CLI)
Sin exposición de sockets de red. Es lanzado directamente por la herramienta cliente:

```bash
python -m mcp_server.magnus_mcp
```

Ejemplo de configuración `.mcp.json` para clientes compatibles:
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

### Modo 2: Transporte HTTP Local
Para clientes que interactúan vía peticiones HTTP / JSON-RPC:

```bash
python -m mcp_server.magnus_http --port 8765
```

Endpoints y Seguridad:
* URL: `POST http://127.0.0.1:8765/mcp`
* **Protección HTTP Guard:** Requiere token `MAGNUS_HTTP_TOKEN` si se habilita fuera de `127.0.0.1`, con Rate Limiting (60 req/min) y restricción estricta de CORS.

---

## 🔒 Privacidad y Gobernanza de Datos

Magnus implementa dos capas de seguridad que **deniegan por defecto**:

1. **Gobernanza de Egreso ([`configs/privacy.yaml`](configs/privacy.yaml)):**
   Define qué namespaces de conocimiento pueden salir del dispositivo. Namespaces como `local_only` (salud mental, finanzas personales) **nunca** son enviados a proveedores de IA en la nube (Anthropic, OpenAI, Google). Si se intenta consultar con un LLM remoto, el motor degrada automáticamente a respuesta extractiva local.
2. **Control de Acceso de Agentes ([`configs/permissions.yaml`](configs/permissions.yaml)):**
   Define el perímetro de lectura exacto de cada agente sobre la wiki y qué herramientas tiene autorizadas para usar.

---

## 🌟 Visión a Futuro y Roadmap

El desarrollo de **Magnus Agent Engine** persigue transformar la interacción hombre-máquina hacia un ecosistema verdaderamente soberano, autónomo y distribuido.

### Próximas Fases del Ecosistema:

1. 🧠 **Embeddings Neuronales Locales Plug & Play (Fase RAG 2.0):**
   * Integración de modelos neuronales ultraligeros de embeddings (p. ej. ONNX / `bge-m3-small`) en el puerto `Embedder` para comprensión semántica profunda sin depender de servicios cloud.
2. 🔄 **Aprendizaje Supervisado y Human-in-the-Loop (Componente 15):**
   * Sistema donde los agentes proponen evoluciones y correcciones a la `LLM-Wiki`, sujetas a la validación y aprobación final del usuario humano.
3. 🌐 **Ecosistema de Agentes Distribuidos (MAS Multi-Node):**
   * Habilitación de agentes Magnus corriendo en nodos de red distribuidos P2P con firma criptográfica de permisos y evidencias.
4. 📱 **Magnus Web & Mobile Interface:**
   * Desarrollo de una interfaz de usuario interactiva y moderna para la administración de agentes, visualización de grafos de memoria SQLite y edición asistida de la LLM-Wiki.

---

## 📄 Licencia y Autoría

* **Autoría & Concepto:** **Yoxe** ([`Magnus Dynamic Group`](https://github.com/YoxeLaunch/MagnusAgentSpecification)).
* **Repositorio Oficial:** [github.com/YoxeLaunch/MagnusAgentSpecification](https://github.com/YoxeLaunch/MagnusAgentSpecification)
