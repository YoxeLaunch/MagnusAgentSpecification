"""Política de egreso de datos de la wiki (paso 5.4 del ROADMAP).

Carga `configs/privacy.yaml` y responde a una sola pregunta: **¿pueden los
pasajes recuperados de estos namespaces salir del dispositivo?**

Denegación por defecto. Un namespace que nadie autorizó explícitamente no
sale, y eso incluye los namespaces nuevos que aparezcan en la wiki. El error
que esto evita es el silencioso: añadir una carpeta de notas médicas y que
empiece a viajar en prompts a un proveedor remoto porque el `default` era
permisivo.

Si el egreso está prohibido y no hay proveedor local disponible, la respuesta
correcta es degradar y declararlo, nunca mandar los datos fuera "porque si no
no se puede responder".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("magnus.privacy")

LOCAL_ONLY = "local_only"
REMOTE_ALLOWED = "remote_allowed"


@dataclass(frozen=True)
class EgressDecision:
    remote_allowed: bool
    reason: str
    blocking_namespaces: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        d = {"egreso_remoto": self.remote_allowed, "razon": self.reason}
        if self.blocking_namespaces:
            d["namespaces_que_bloquean"] = list(self.blocking_namespaces)
        return d


class EgressPolicy:
    def __init__(self, por_defecto: str = LOCAL_ONLY,
                 namespaces: dict[str, str] | None = None,
                 proveedores_locales: set[str] | None = None, *, activa: bool = True):
        self.por_defecto = por_defecto
        self._namespaces = namespaces or {}
        self.proveedores_locales = proveedores_locales or {"ollama"}
        self.activa = activa

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EgressPolicy":
        p = Path(path)
        if not p.exists():
            # Sin archivo, la postura segura es la restrictiva, no la permisiva.
            log.warning("no existe %s — se asume local_only para todo", p)
            return cls(activa=False)
        raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("egreso") or {}
        return cls(
            por_defecto=raw.get("por_defecto", LOCAL_ONLY),
            namespaces=dict(raw.get("namespaces") or {}),
            proveedores_locales=set(raw.get("proveedores_locales") or ["ollama"]),
        )

    # -- API ------------------------------------------------------------------
    def politica_de(self, namespace: str) -> str:
        return self._namespaces.get(namespace, self.por_defecto)

    def es_local(self, provider: str) -> bool:
        return provider in self.proveedores_locales

    def check(self, namespaces: list[str]) -> EgressDecision:
        """¿Puede el contenido de estos namespaces ir a un proveedor remoto?

        Basta con que UNO lo prohíba para bloquear: el prompt es uno solo y
        viaja entero.
        """
        bloquean = tuple(ns for ns in namespaces
                         if self.politica_de(ns) != REMOTE_ALLOWED)
        if bloquean:
            return EgressDecision(
                False,
                f"la política de privacidad marca {list(bloquean)} como '{LOCAL_ONLY}'",
                bloquean)
        return EgressDecision(True, "todos los namespaces autorizan egreso remoto")
