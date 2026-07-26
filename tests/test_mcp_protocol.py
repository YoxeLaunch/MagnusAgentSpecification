"""Integración de punta a punta: JSON-RPC MCP → MagnusEngine → wiki.

Recorre el mismo despacho que usan los dos transportes (stdio y HTTP) sin
abrir un socket, sin red y sin credenciales. Es la prueba que faltaba: hasta
ahora la única verificación del circuito eran las demos, que sustituyen el
motor entero por maquetas.
"""
from __future__ import annotations

import json

import pytest

from mcp_server import protocol
from orchestration.engine import MagnusEngine


@pytest.fixture
def mcp(mini_root):
    """Despacho MCP apuntando al proyecto mínimo, en modo extractivo."""
    protocol.set_engine(MagnusEngine(mini_root))
    yield protocol
    protocol.set_engine(None)


def _call(mcp, name: str, args: dict | None = None, id_: int = 1) -> dict:
    return mcp.handle({"jsonrpc": "2.0", "id": id_, "method": "tools/call",
                       "params": {"name": name, "arguments": args or {}}})


def test_initialize_anuncia_protocolo_y_servidor(mcp):
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"]
    assert "tools" in resp["result"]["capabilities"]


def test_una_notificacion_no_produce_respuesta(mcp):
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_declara_las_herramientas(mcp):
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    nombres = {t["name"] for t in resp["result"]["tools"]}
    assert nombres == {"magnus_ask", "magnus_list_agents"}


def test_metodo_desconocido_devuelve_error_jsonrpc(mcp):
    resp = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "no_existe"})
    assert resp["error"]["code"] == -32601


def test_magnus_list_agents_devuelve_los_agentes_cargados(mcp):
    resp = _call(mcp, "magnus_list_agents")
    agentes = json.loads(resp["result"]["content"][0]["text"])
    assert {a["id"] for a in agentes} == {"fina", "dormi", "vacio"}


def test_magnus_ask_recorre_el_circuito_y_cita_la_wiki(mcp):
    resp = _call(mcp, "magnus_ask", {"pregunta": "cuál es la inflación en República Dominicana"})
    texto = resp["result"]["content"][0]["text"]
    assert "isError" not in resp["result"]
    assert "Inflacion RD.md" in texto, "la respuesta debe citar la nota real recuperada"


def test_magnus_ask_con_agente_forzado_usa_solo_su_parcela(mcp):
    resp = _call(mcp, "magnus_ask",
                 {"pregunta": "cómo mejorar mi sueño", "agente": "dormi"})
    texto = resp["result"]["content"][0]["text"]
    assert "Agentes: dormi" in texto
    assert "01-Finanzas" not in texto


def test_una_herramienta_desconocida_se_reporta_como_error_de_herramienta(mcp):
    resp = _call(mcp, "herramienta_inventada")
    assert resp["result"]["isError"] is True
    assert "desconocida" in resp["result"]["content"][0]["text"]


# -- paso 5: rol del llamante y auditoría --------------------------------------
def test_el_rol_del_llamante_debe_autorizar_la_herramienta(mini_root):
    """`operator` puede consultar; un rol sin permisos, no."""
    from orchestration.engine import MagnusEngine

    protocol.set_engine(MagnusEngine(mini_root, caller_role="rol_sin_permisos"))
    try:
        resp = _call(protocol, "magnus_ask", {"pregunta": "lo que sea"})
        assert resp["result"]["isError"] is True
        assert "no está autorizado" in resp["result"]["content"][0]["text"]
    finally:
        protocol.set_engine(None)


def test_cada_llamada_a_herramienta_queda_auditada(mini_root, tmp_path):
    from orchestration.audit import JsonlTraceStore
    from orchestration.engine import MagnusEngine

    destino = tmp_path / "traces"
    protocol.set_engine(MagnusEngine(mini_root, trace_store=JsonlTraceStore(destino)))
    try:
        _call(protocol, "magnus_list_agents")
        _call(protocol, "herramienta_inventada")
    finally:
        protocol.set_engine(None)

    lineas = [json.loads(l) for l in
              list(destino.glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()]
    llamadas = [e for e in lineas if e.get("tipo") == "tool_call"]

    assert {(e["herramienta"], e["resultado"]) for e in llamadas} == {
        ("magnus_list_agents", "ok"), ("herramienta_inventada", "error")}
    assert all(e["rol"] == "operator" for e in llamadas)
    assert all("ts" in e for e in llamadas)
