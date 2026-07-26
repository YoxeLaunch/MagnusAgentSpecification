"""Servidor MCP de Magnus — transporte HTTP (Streamable HTTP), SOLO stdlib.

Para apps que exigen una "URL de servidor MCP remoto" en vez de un comando
local (p.ej. el diálogo "Agregar conector personalizado" de Claude). Expone
un único endpoint POST /mcp que recibe JSON-RPC y devuelve JSON-RPC — misma
lógica que magnus_mcp.py (stdio), importada de protocol.py.

Debes dejar este proceso corriendo mientras quieras usar el conector: aquí no
lo lanza la app por ti (a diferencia del stdio, que sí lo hace).

Ejecutar:  python -m mcp_server.magnus_http [--port 8765] [--host 127.0.0.1]
Luego en la app: URL del servidor MCP remoto = http://127.0.0.1:8765/mcp

Los controles de acceso (auth, CORS, límites) viven en `http_guard.py`.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # instalado como paquete (`python -m mcp_server.magnus_http`)
    from mcp_server.http_guard import MAX_CONCURRENCY, ConfiguracionInsegura, HttpGuard
    from mcp_server.protocol import get_engine, handle, log
except ImportError:  # ejecutado como script suelto
    from http_guard import MAX_CONCURRENCY, ConfiguracionInsegura, HttpGuard
    from protocol import get_engine, handle, log

DEFAULT_PORT = 8765
PATH = "/mcp"

GUARD: HttpGuard | None = None
#: Tope de peticiones en vuelo. Cada una ingiere contexto y puede llamar a un
#: proveedor; sin tope, N clientes concurrentes multiplican memoria y coste.
_SEMAFORO = threading.Semaphore(MAX_CONCURRENCY)


class MCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silenciar el log por defecto; usamos el nuestro
        log(f"{self.address_string()} " + (fmt % args))

    # -- utilidades -----------------------------------------------------------
    @property
    def _cliente(self) -> str:
        return self.client_address[0] if self.client_address else "desconocido"

    def _cors(self) -> None:
        origen = GUARD.origen_permitido(self.headers.get("Origin"))
        if origen is not None:
            self.send_header("Access-Control-Allow-Origin", origen)
            self.send_header("Vary", "Origin")

    def _send_json(self, status: int, payload) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status: int, mensaje: str, code: int = -32600) -> None:
        self._send_json(status, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": code, "message": mensaje}})

    # -- verbos ---------------------------------------------------------------
    def do_OPTIONS(self):  # preflight CORS, por si el cliente lo hace vía fetch
        self.send_response(204)
        self._cors()
        if GUARD.origins:
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization, Mcp-Session-Id, Accept")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") != PATH:
            self._send_json(404, {"error": "ruta no encontrada, usa /mcp"})
            return

        ok, motivo = GUARD.dentro_de_tasa(self._cliente)
        if not ok:
            log(f"rate limit para {self._cliente}: {motivo}")
            self._error(429, f"demasiadas peticiones: {motivo}")
            return

        ok, motivo = GUARD.autoriza(self.headers.get("Authorization"))
        if not ok:
            log(f"no autorizado desde {self._cliente}: {motivo}")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="magnus"')
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        ok, motivo = GUARD.cuerpo_admisible(length)
        if not ok:
            self._error(413, f"petición demasiado grande: {motivo}")
            return

        raw = self.rfile.read(length) if length else b""
        try:
            msg = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "parse error", code=-32700)
            return

        with _SEMAFORO:
            if isinstance(msg, list):  # lote JSON-RPC
                responses = [r for r in (handle(m) for m in msg) if r is not None]
                self._send_json(202 if not responses else 200, responses or None)
                return

            response = handle(msg)

        if response is None:
            self._send_json(202, None)   # notificación: sin cuerpo de respuesta
        else:
            self._send_json(200, response)

    def do_GET(self):
        # Servidor sin notificaciones push: no ofrecemos stream SSE persistente.
        self._send_json(405, {"error": "GET no soportado (servidor sin streaming push)"})

    def do_DELETE(self):
        self._send_json(204, None)  # cierre de sesión: no-op (servidor sin estado)


def main() -> None:
    global GUARD
    port = DEFAULT_PORT
    host = None
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]

    try:
        GUARD = HttpGuard.from_env(host)
    except ConfiguracionInsegura as e:
        log(f"ARRANQUE ABORTADO: {e}")
        raise SystemExit(2)

    log(f"controles de acceso: {GUARD.resumen()}")
    if GUARD.es_local and GUARD.token is None:
        log("aviso: sin token. Correcto para uso local; obligatorio para salir de 127.0.0.1.")

    get_engine()  # ingesta e inicialización antes de abrir el puerto
    server = ThreadingHTTPServer((GUARD.host, port), MCPHandler)
    log(f"escuchando en http://{GUARD.host}:{port}{PATH}  (deja esta ventana abierta)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("detenido")


if __name__ == "__main__":
    main()
