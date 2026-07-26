"""Servidor MCP de Magnus — transporte stdio, SOLO stdlib.

Habla el protocolo que usan Claude Code (CLI/Desktop app de sesiones), Codex
CLI y Antigravity: JSON-RPC delimitado por saltos de línea sobre stdin/stdout.
La lógica de herramientas vive en `protocol.py` (compartida con el transporte
HTTP en `magnus_http.py`).

REGLA stdio: stdout es SOLO para el protocolo; los logs van a stderr.

Ejecutar (lo lanza la app, no tú a mano):  python -m mcp_server.magnus_mcp
"""
from __future__ import annotations

import json
import sys

# MCP exige UTF-8 en stdio; Windows por defecto usa cp1252 → forzamos UTF-8.
for _stream in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:  # instalado como paquete (`python -m mcp_server.magnus_mcp`)
    from mcp_server.protocol import get_engine, handle, log  # noqa: E402
except ImportError:  # ejecutado como script suelto (`python mcp_server/magnus_mcp.py`)
    from protocol import get_engine, handle, log  # noqa: E402


def main() -> None:
    get_engine()  # ingesta e inicialización antes de aceptar peticiones
    log("escuchando stdio (JSON-RPC)")
    for line in sys.stdin:
        # Algunos clientes en Windows (PowerShell/.NET) anteponen un BOM
        # (U+FEFF) a la primera línea escrita a stdin. `utf-8` no lo retira
        # (solo `utf-8-sig` lo haría, y reconfigurar el encoding no es
        # seguro a mitad de stream) — lo quitamos aquí explícitamente, si
        # no la primera petición (initialize) se descarta en silencio y el
        # cliente nunca recibe respuesta.
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"línea no-JSON ignorada: {line[:80]}")
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
