"""Lógica MCP compartida entre transportes (stdio y HTTP).

Único lugar de verdad: herramientas, motor Magnus y despacho JSON-RPC. Tanto
`magnus_mcp.py` (stdio, para CLI/Antigravity/Codex) como `magnus_http.py`
(Streamable HTTP, para apps que piden una URL de "conector remoto") importan
de aquí, así que nunca divergen en comportamiento.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# el proyecto es la raíz (carpeta padre de mcp_server/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestration.audit import JsonlTraceStore, trace_store_from_env  # noqa: E402
from orchestration.engine import MagnusEngine  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "magnus-dynamic-group", "version": "1.0.0"}


def log(msg: str) -> None:
    print(f"[magnus-mcp] {msg}", file=sys.stderr, flush=True)


# --- carga del motor (ingesta de la wiki al arrancar) ---------------------
def _build_provider_registry():
    """Construye el `ProviderRegistry` según `MAGNUS_PROVIDER`.

    Valores: vacío/`none` → extractivo (sin LLM, sin coste). `ollama`,
    `anthropic` → solo ese adaptador. `auto` → todos los que estén
    instalados y configurados.

    Nunca decide el modelo: eso lo dice el perfil de cada agente resuelto
    contra `configs/models.yaml`. Aquí solo se decide QUÉ ADAPTADORES existen.
    """
    modo = os.environ.get("MAGNUS_PROVIDER", "").strip().lower()
    if modo in ("", "none", "extractivo", "extractive"):
        log("proveedor: extractivo (sin LLM, sin coste)")
        return None

    adaptadores = {}
    if modo in ("ollama", "auto"):
        from providers.ollama_provider import OllamaProvider
        adaptadores["ollama"] = OllamaProvider()
        log("adaptador disponible: ollama (local)")
    if modo in ("anthropic", "auto"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log("anthropic omitido: falta ANTHROPIC_API_KEY")
        else:
            try:
                from providers.anthropic_provider import AnthropicProvider
                adaptadores["anthropic"] = AnthropicProvider()
                log("adaptador disponible: anthropic")
            except ImportError:
                log("anthropic omitido: falta el paquete ('pip install magnus[anthropic]')")

    if not adaptadores:
        log(f"MAGNUS_PROVIDER='{modo}' no produjo ningún adaptador → modo extractivo")
        return None

    from providers.registry import ProviderRegistry
    return ProviderRegistry.from_yaml(
        os.path.join(ROOT, "configs", "models.yaml"), adaptadores)


def build_engine() -> MagnusEngine:
    log("iniciando… cargando agentes (Agent Registry) e ingiriendo LLM-Wiki")
    # El registro auditable es opt-in: la wiki contiene datos personales y
    # nada se escribe a disco salvo que se pida (ver orchestration/audit.py).
    trace_store = trace_store_from_env()
    if isinstance(trace_store, JsonlTraceStore):
        log(f"registro auditable ACTIVO en {trace_store.directory}")
    engine = MagnusEngine(ROOT, providers=_build_provider_registry(),
                          trace_store=trace_store)
    log(f"agentes activos: {[a['id'] for a in engine.list_agents()]}")
    for v in engine.load_report.invalid:
        log(f"agente inválido «{v.agent_id}»: {'; '.join(v.errors)}")
    log(f"listo. Ingesta: {engine.ingest_report}")
    return engine


_ENGINE: MagnusEngine | None = None


def get_engine() -> MagnusEngine:
    """Motor perezoso: importar este módulo ya no ingiere la wiki entera.

    Antes el motor se construía en tiempo de import, lo que hacía imposible
    importar el despacho JSON-RPC en un test sin pagar la ingesta real (y sin
    poder sustituirlo por un proyecto de fixtures)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = build_engine()
    return _ENGINE


def set_engine(engine: MagnusEngine | None) -> None:
    """Punto de inyección para tests: fija (o descarta con None) el motor."""
    global _ENGINE
    _ENGINE = engine


# --- definición de herramientas ------------------------------------------
TOOLS = [
    {
        "name": "magnus_ask",
        "description": (
            "Consulta el sistema multiagente Magnus sobre tu base de conocimiento "
            "personal (LLM-Wiki). Enruta la pregunta a los agentes especializados "
            "(economía, salud mental, proyectos, salud corporal, tecnología) y "
            "devuelve un informe con citas a tus propias notas."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pregunta": {"type": "string", "description": "La pregunta del usuario."},
                "agente": {"type": "string",
                           "description": "Opcional: forzar un agente concreto (id)."},
            },
            "required": ["pregunta"],
        },
    },
    {
        "name": "magnus_list_agents",
        "description": "Lista los agentes Magnus disponibles y las carpetas de la wiki que cubren.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --- despacho de herramientas --------------------------------------------
#: Acción de `permissions.yaml::roles` que exige cada herramienta MCP. Sin
#: entrada aquí, la herramienta se deniega: no hay "permitida por omisión".
ACCION_REQUERIDA = {
    "magnus_ask": "query",
    "magnus_list_agents": "agents.read",
}


def call_tool(name: str, args: dict, *, rol: str | None = None) -> str:
    engine = get_engine()
    rol = rol or engine.caller_role

    accion = ACCION_REQUERIDA.get(name)
    if accion is None:
        raise ValueError(f"herramienta desconocida: {name}")
    if not engine.permissions.rol_puede(rol, accion):
        raise PermissionError(
            f"el rol '{rol}' no está autorizado para '{accion}' "
            f"(requerido por la herramienta '{name}')")

    if name == "magnus_ask":
        r = engine.ask(args["pregunta"], agent_id=args.get("agente") or None)
        fuentes = "\n".join(f"- {s}" for s in r["fuentes"][:12])
        return (f"{r['respuesta']}\n\n"
                f"— Agentes: {', '.join(r['agentes'])}\n"
                f"— Fuentes citadas (de tu wiki):\n{fuentes}")
    return json.dumps(engine.list_agents(), ensure_ascii=False, indent=2)


def _auditar(name: str, rol: str, ok: bool, detalle: str = "") -> None:
    """Quién, qué herramienta, cuándo y con qué resultado (paso 5.5).

    Reusa el mismo registro trazable del paso 3 y su misma postura de
    privacidad: no se guardan los argumentos, que para `magnus_ask` son la
    pregunta del usuario y ya quedan registrados —con su contexto de
    evaluación— dentro del motor.
    """
    try:
        get_engine().trace_store.record({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tipo": "tool_call",
            "herramienta": name,
            "rol": rol,
            "resultado": "ok" if ok else "error",
            "detalle": detalle,
        })
    except Exception as e:  # la auditoría nunca debe tumbar la petición
        log(f"no se pudo auditar la llamada a {name}: {e}")


# --- envoltorios JSON-RPC -------------------------------------------------
def make_result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def make_error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg: dict):
    """Despacha un único mensaje JSON-RPC. Devuelve None para notificaciones
    (sin id) — el transporte decide cómo representar la ausencia de respuesta.
    """
    method = msg.get("method")
    id_ = msg.get("id")

    if method == "initialize":
        return make_result(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return make_result(id_, {})
    if method == "tools/list":
        return make_result(id_, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        rol = get_engine().caller_role
        try:
            text = call_tool(name, args, rol=rol)
        except Exception as e:
            log(f"error en {name}: {e}")
            _auditar(name, rol, ok=False, detalle=f"{type(e).__name__}: {e}")
            return make_result(id_, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
        _auditar(name, rol, ok=True)
        return make_result(id_, {"content": [{"type": "text", "text": text}]})
    if id_ is not None:
        return make_error(id_, -32601, f"método no soportado: {method}")
    return None
