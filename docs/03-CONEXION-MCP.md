# Conectar Magnus a Claude Code, Codex, Antigravity y Claude Desktop

## Aclaración clave: MCP ≠ API de pago

Son dos cosas distintas que suenan parecido:

| | ¿Qué es? | ¿Cuesta dinero? |
|---|---|---|
| **API de Anthropic/OpenAI** (`console.anthropic.com`, `platform.openai.com`) | Acceso por token a un modelo, facturado aparte de tu suscripción de chat | **Sí**, pago por uso |
| **MCP** (Model Context Protocol) | Protocolo local (JSON-RPC sobre stdio) para que una app llame a tu código como "herramienta" | **No**, es gratis — corre en tu PC |

`mcp_server/magnus_mcp.py` habla MCP. **No necesitas ninguna API de pago para usarlo.**

## Confirmado: los tres agentes de código usan tu suscripción, no una API aparte

Verificado ahora mismo (no supuesto):

| Herramienta | Autenticación sin coste extra | Config de MCP |
|---|---|---|
| **Claude Code** (aquí) | Login con Claude Pro/Max/Team — sin API key | `.mcp.json` (proyecto) o `claude mcp add` |
| **Codex CLI** (OpenAI) | "Sign in with ChatGPT" — usa cuota de tu plan Plus/Pro/Business, **no** la factura de la API | `~/.codex/config.toml` o `.codex/config.toml` (proyecto) |
| **Antigravity** (Google) | Cuenta Google AI Pro/Ultra (suscripción) | `~/.gemini/config/mcp_config.json` |

Es decir: sí puedes ejecutar todo esto desde Claude Code, Codex o Antigravity **sin pagar una API separada**, en dos modos distintos (abajo).

---

## Modo 0 — el más simple: pídeselo directamente al agente (sin configurar nada)

Claude Code, Codex CLI y Antigravity **ya tienen terminal y acceso a archivos** — son agentes de código, exactamente como esta sesión. No necesitas MCP para "probarlo": basta con pedirle al agente, dentro del proyecto:

> "Ejecuta `demo/run_demo.py` y explícame el resultado"
> "Llama a `orchestration.engine.MagnusEngine` y pregúntale sobre X"

Esto es lo que hemos estado haciendo en esta conversación. Cero configuración, cero coste adicional: usa la misma sesión/cuota que ya tienes abierta.

**Usa este modo para seguir iterando y probando.** Usa el Modo 1 (MCP) cuando quieras que la app trate a Magnus como una *herramienta* fija disponible en cualquier chat, sin tener que explicarle el proyecto cada vez.

---

## Modo 1 — registrar el servidor MCP (herramienta persistente)

### Claude Code

```bash
claude mcp add magnus -- C:\Python314\python.exe C:\MagnusAgent\mcp_server\magnus_mcp.py
```

O copia `.mcp.json.example` a `.mcp.json` y ajusta las rutas a tu entorno local:

```json
{
  "mcpServers": {
    "magnus": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server.magnus_mcp"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Verifica con `/mcp` dentro de Claude Code.

### Codex CLI (ChatGPT)

Edita `~/.codex/config.toml` (o `.codex/config.toml` en la raíz del proyecto para que aplique solo aquí):

```toml
[mcp_servers.magnus]
command = "C:\\Python314\\python.exe"
args = ["C:\\MagnusAgent\\mcp_server\\magnus_mcp.py"]
env = { "PYTHONIOENCODING" = "utf-8" }
```

También existe un asistente interactivo: ejecuta `codex mcp add` y sigue las
indicaciones (tipo STDIO, comando, argumentos).

### Antigravity (Google)

Edita `~/.gemini/config/mcp_config.json` (compartido entre Antigravity IDE y
Antigravity CLI):

```json
{
  "mcpServers": {
    "magnus": {
      "command": "C:\\Python314\\python.exe",
      "args": ["C:\\MagnusAgent\\mcp_server\\magnus_mcp.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

### Claude Desktop (la app de chat, no el CLI)

`C:\Users\JoseO\AppData\Roaming\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "magnus": {
      "command": "C:\\Python314\\python.exe",
      "args": ["C:\\MagnusAgent\\mcp_server\\magnus_mcp.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Reinicia la app correspondiente tras editar. En cualquiera de los cuatro,
pregunta luego:

> Usa magnus_ask: ¿qué dice mi wiki sobre invertir dinero estando estresado?

---

## Activar respuestas generadas (Ollama, gratis y local)

Por defecto Magnus responde en **modo extractivo** (cita pasajes reales de tu
wiki, sin LLM, sin coste). Para respuestas redactadas por un modelo, sin coste
y en tu máquina:

1. Instala Ollama: https://ollama.com
2. Descarga un modelo: `ollama pull qwen2.5:7b`
3. Añade al bloque `env` de la config que uses: `"MAGNUS_PROVIDER": "ollama"`

Reinicia. Un LLM local redacta las respuestas **sobre la evidencia
recuperada** de tu wiki (sigue citando fuentes). Coste: $0.

> Para usar la API de Anthropic (Claude, máxima calidad) en su lugar: eso **sí
> tiene coste por token**, aparte de tu plan. Se configura en
> `configs/models.yaml` (profile → provider: anthropic) + `ANTHROPIC_API_KEY`.
> Resérvalo para consultas puntuales donde quieras la mejor calidad posible.

---

## Probar el servidor a mano (sin ninguna app)

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"magnus_ask","arguments":{"pregunta":"resumen de mi wiki sobre finanzas"}}}' \
  | python mcp_server/magnus_mcp.py
```

Los logs salen por stderr; las respuestas del protocolo por stdout.

---

## ¿Y ChatGPT (la app de chat, no Codex)?

La app de chat de ChatGPT no lanza servidores MCP locales como Codex CLI sí
hace. Conecta vía **Action de un GPT personalizado (OpenAPI)** o conectores
hospedados — requiere exponer Magnus como **API REST**
(`POST /v1/query`, Componente 12) accesible desde internet (túnel tipo ngrok o
servidor). El motor (`orchestration/engine.py`) ya está listo para envolverlo
en FastAPI si lo necesitas.

**Codex CLI sí funciona ya** con la config de arriba — es un agente de
terminal, no la app de chat.
