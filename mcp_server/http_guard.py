"""Controles de transporte del servidor MCP HTTP (pasos 5.2, 5.3 y 5.6).

Separado de `magnus_http.py` porque son decisiones de política, no de
transporte, y así se pueden testear sin abrir un socket.

Postura por defecto
-------------------
- **Bind a `127.0.0.1`.** Salir de ahí exige token: el servidor se **niega a
  arrancar** en una interfaz pública sin `MAGNUS_HTTP_TOKEN`. Antes el único
  motivo por el que esto no era un agujero era que nadie había cambiado la
  dirección de escucha — una mitigación que dependía de que nadie tocara una
  línea.
- **Sin CORS.** Se responde `Access-Control-Allow-Origin` solo a orígenes que
  alguien listó explícitamente en `MAGNUS_HTTP_ORIGINS`. El `*` anterior
  autorizaba a cualquier página web abierta en el navegador a interrogar la
  wiki personal del usuario.
- **Límites siempre activos**, también en localhost: tamaño de petición, tasa
  por cliente y concurrencia. Un bucle accidental en un cliente MCP no debería
  poder tumbar el proceso.
"""
from __future__ import annotations

import hmac
import os
import time
from dataclasses import dataclass, field

LOCALHOST = {"127.0.0.1", "::1", "localhost"}

MAX_BODY_BYTES = 1024 * 1024        # 1 MiB
RATE_LIMIT_REQUESTS = 60            # por ventana
RATE_LIMIT_WINDOW_S = 60.0
MAX_CONCURRENCY = 8


class ConfiguracionInsegura(RuntimeError):
    """El servidor no puede arrancar con esta combinación de opciones."""


@dataclass
class HttpGuard:
    host: str = "127.0.0.1"
    token: str | None = None
    origins: tuple[str, ...] = ()
    max_body_bytes: int = MAX_BODY_BYTES
    rate_limit: int = RATE_LIMIT_REQUESTS
    window_s: float = RATE_LIMIT_WINDOW_S
    _hits: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _reloj: object = time.monotonic

    def __post_init__(self) -> None:
        if not self.es_local and not self.token:
            raise ConfiguracionInsegura(
                f"escuchar en '{self.host}' expone Magnus fuera de este equipo y la "
                f"wiki contiene datos personales. Define MAGNUS_HTTP_TOKEN (y usa un "
                f"valor largo y aleatorio) o deja el bind en 127.0.0.1.")

    @classmethod
    def from_env(cls, host: str | None = None) -> "HttpGuard":
        origenes = tuple(
            o.strip() for o in os.environ.get("MAGNUS_HTTP_ORIGINS", "").split(",")
            if o.strip())
        return cls(
            host=host or os.environ.get("MAGNUS_HTTP_HOST", "127.0.0.1"),
            token=os.environ.get("MAGNUS_HTTP_TOKEN") or None,
            origins=origenes,
        )

    @property
    def es_local(self) -> bool:
        return self.host in LOCALHOST

    # -- autenticación --------------------------------------------------------
    def autoriza(self, cabecera_authorization: str | None) -> tuple[bool, str]:
        """Comprueba el token compartido si hay uno configurado."""
        if self.token is None:
            return True, "sin autenticación (bind local sin token configurado)"
        recibido = ""
        if cabecera_authorization:
            partes = cabecera_authorization.split(None, 1)
            if len(partes) == 2 and partes[0].lower() == "bearer":
                recibido = partes[1].strip()
        # Comparación en tiempo constante: un `==` filtra el token por
        # el tiempo de respuesta ante un atacante paciente.
        if recibido and hmac.compare_digest(recibido, self.token):
            return True, "token válido"
        return False, "token ausente o inválido"

    # -- CORS -----------------------------------------------------------------
    def origen_permitido(self, origen: str | None) -> str | None:
        """Devuelve el valor de `Access-Control-Allow-Origin`, o None.

        Nunca `*`: se refleja el origen concreto solo si está en la lista.
        """
        if not origen or origen not in self.origins:
            return None
        return origen

    # -- límites --------------------------------------------------------------
    def cuerpo_admisible(self, longitud: int) -> tuple[bool, str]:
        if longitud > self.max_body_bytes:
            return False, f"cuerpo de {longitud} bytes (máximo {self.max_body_bytes})"
        return True, ""

    def dentro_de_tasa(self, cliente: str) -> tuple[bool, str]:
        ahora = self._reloj()
        ventana = self._hits.setdefault(cliente, [])
        limite = ahora - self.window_s
        ventana[:] = [t for t in ventana if t > limite]
        if len(ventana) >= self.rate_limit:
            return False, (f"{len(ventana)} peticiones en {self.window_s:.0f}s "
                           f"(máximo {self.rate_limit})")
        ventana.append(ahora)
        return True, ""

    def resumen(self) -> str:
        return (f"host={self.host} auth={'token' if self.token else 'ninguna'} "
                f"cors={list(self.origins) or 'ninguno'} "
                f"limite={self.rate_limit}/{self.window_s:.0f}s "
                f"cuerpo_max={self.max_body_bytes}B")
