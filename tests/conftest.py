"""Configuración común de la suite.

Dos garantías que la suite entera debe cumplir (criterio de hecho del paso 1
del ROADMAP): corre en un checkout limpio, y corre sin red ni credenciales.
La segunda se hace explícita aquí borrando del entorno cualquier clave que la
máquina del desarrollador pudiera tener puesta — si algún test intentara usar
un proveedor real, fallará por credencial ausente en vez de gastar dinero o
salir a internet en silencio.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # permite `pytest` sin `pip install -e .`
    sys.path.insert(0, str(ROOT))

from magnus_fixtures.mini_project import build_mini_project  # noqa: E402

_CLAVES_DE_PROVEEDOR = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
    "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "MAGNUS_PROVIDER",
]


@pytest.fixture(autouse=True)
def sin_credenciales(monkeypatch):
    for clave in _CLAVES_DE_PROVEEDOR:
        monkeypatch.delenv(clave, raising=False)


@pytest.fixture(scope="session")
def mini_root(tmp_path_factory) -> Path:
    """Proyecto Magnus mínimo y completo, construido una vez por sesión.

    Es de solo lectura para los tests; quien necesite mutarlo (p. ej. cambiar
    un `agent.yaml` para probar validación) debe usar `mini_root_mutable`.
    """
    return build_mini_project(tmp_path_factory.mktemp("magnus_mini"))


@pytest.fixture
def mini_root_mutable(tmp_path) -> Path:
    """Copia fresca del proyecto mínimo, aislada por test."""
    return build_mini_project(tmp_path / "proyecto")


@pytest.fixture
def repo_root() -> Path:
    """Raíz del repositorio real (para tests sobre configs/ y agents/ reales)."""
    return ROOT
