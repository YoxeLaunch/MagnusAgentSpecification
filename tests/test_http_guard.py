"""Pasos 5.2, 5.3 y 5.6: autenticación, CORS y límites del servidor MCP HTTP.

Estado de partida: `Access-Control-Allow-Origin: *` en todas las respuestas,
cero autenticación y cero límites de tasa, mitigado únicamente por atarse a
127.0.0.1 — una protección que dependía de que nadie tocara esa línea.
"""
from __future__ import annotations



import pytest

from mcp_server.http_guard import ConfiguracionInsegura, HttpGuard


# -- bind y autenticación ---------------------------------------------------------
def test_en_localhost_arranca_sin_token():
    guard = HttpGuard(host="127.0.0.1")
    assert guard.es_local is True
    assert guard.autoriza(None)[0] is True


def test_fuera_de_localhost_sin_token_no_arranca():
    with pytest.raises(ConfiguracionInsegura) as exc:
        HttpGuard(host="0.0.0.0")

    assert "MAGNUS_HTTP_TOKEN" in str(exc.value)


def test_fuera_de_localhost_con_token_si_arranca():
    guard = HttpGuard(host="0.0.0.0", token="un-token-largo-y-aleatorio")
    assert guard.es_local is False


@pytest.mark.parametrize("cabecera", [
    None, "", "Bearer", "Bearer ", "Basic abc", "Bearer token-equivocado",
    "bearer otro-token",
])
def test_rechaza_credenciales_invalidas(cabecera):
    guard = HttpGuard(host="0.0.0.0", token="el-token-bueno")
    assert guard.autoriza(cabecera)[0] is False


@pytest.mark.parametrize("cabecera", ["Bearer el-token-bueno", "bearer el-token-bueno"])
def test_acepta_el_token_correcto(cabecera):
    guard = HttpGuard(host="0.0.0.0", token="el-token-bueno")
    assert guard.autoriza(cabecera)[0] is True


def test_lee_la_configuracion_del_entorno(monkeypatch):
    monkeypatch.setenv("MAGNUS_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("MAGNUS_HTTP_TOKEN", "secreto")
    monkeypatch.setenv("MAGNUS_HTTP_ORIGINS", "https://a.example, https://b.example")

    guard = HttpGuard.from_env()

    assert guard.host == "0.0.0.0"
    assert guard.origins == ("https://a.example", "https://b.example")


# -- CORS ---------------------------------------------------------------------------
def test_sin_origenes_configurados_no_se_emite_cors():
    guard = HttpGuard()
    assert guard.origen_permitido("https://cualquiera.example") is None
    assert guard.origen_permitido(None) is None


def test_solo_se_refleja_un_origen_de_la_lista():
    guard = HttpGuard(origins=("https://mi-app.example",))

    assert guard.origen_permitido("https://mi-app.example") == "https://mi-app.example"
    assert guard.origen_permitido("https://sitio-cualquiera.example") is None


def test_nunca_devuelve_el_comodin():
    guard = HttpGuard(origins=("*",))
    # '*' como origen literal no coincide con ningún Origin real de navegador
    assert guard.origen_permitido("https://sitio.example") is None


# -- límites --------------------------------------------------------------------------
def test_rechaza_cuerpos_demasiado_grandes():
    guard = HttpGuard(max_body_bytes=100)

    assert guard.cuerpo_admisible(50)[0] is True
    assert guard.cuerpo_admisible(101)[0] is False


def test_limita_la_tasa_por_cliente():
    guard = HttpGuard(rate_limit=3)

    assert all(guard.dentro_de_tasa("10.0.0.1")[0] for _ in range(3))
    assert guard.dentro_de_tasa("10.0.0.1")[0] is False
    # otro cliente no queda penalizado por el primero
    assert guard.dentro_de_tasa("10.0.0.2")[0] is True


def test_la_ventana_de_tasa_se_desliza():
    reloj = iter([0.0, 30.0, 50.0, 70.0]).__next__
    guard = HttpGuard(rate_limit=2, window_s=60.0)
    guard._reloj = reloj

    assert guard.dentro_de_tasa("c")[0] is True   # t=0  · ventana vacía
    assert guard.dentro_de_tasa("c")[0] is True   # t=30 · 1 en ventana
    assert guard.dentro_de_tasa("c")[0] is False  # t=50 · las de t=0 y t=30 cuentan
    assert guard.dentro_de_tasa("c")[0] is True   # t=70 · la de t=0 ya expiró
