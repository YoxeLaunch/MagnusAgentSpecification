"""Guardrails por dominio y escalado por urgencia (paso 3.4 del ROADMAP).

Carga `configs/guardrails.yaml`. Dos responsabilidades separadas:

  - **Aviso de dominio**: los agentes de salud y finanzas responden dentro de
    unos límites que deben quedar dichos en la respuesta, no implícitos en la
    documentación. El dominio se deduce de las capacidades que el agente
    declara en su `agent.yaml` — el enrutado ya decidió de qué trata esto.
  - **Escalado por urgencia**: ante ciertas señales en la consulta, la
    respuesta correcta no es una respuesta mejor documentada sino una vía de
    contacto humano. Se comprueba ANTES de recuperar evidencia o llamar a
    ningún modelo, y corta el flujo.

Si el archivo no existe, los guardrails quedan inactivos y se dice
explícitamente (`Guardrails.activo == False`) en vez de fallar silenciosamente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ACENTOS = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
            ("ü", "u"), ("ñ", "n"))


def _norm(texto: str) -> str:
    t = texto.lower()
    for a, b in _ACENTOS:
        t = t.replace(a, b)
    return t


@dataclass(frozen=True)
class Urgencia:
    id: str
    patrones: list[str]
    respuesta: str
    capacidades: list[str] = field(default_factory=list)   # vacío = aplica siempre


@dataclass(frozen=True)
class Dominio:
    id: str
    capacidades: list[str]
    aviso: str


@dataclass(frozen=True)
class GuardrailCheck:
    """Resultado de evaluar una consulta contra los guardrails."""
    escalate: bool = False
    urgencia_id: str | None = None
    mensaje: str = ""              # qué responder si `escalate`
    dominios: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d: dict = {"dominios": self.dominios}
        if self.escalate:
            d["escalado"] = self.urgencia_id
        if self.avisos:
            d["avisos"] = len(self.avisos)
        return d


class Guardrails:
    def __init__(self, dominios: list[Dominio], urgencias: list[Urgencia], *, activo: bool = True):
        self._dominios = dominios
        self._urgencias = urgencias
        self.activo = activo

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Guardrails":
        p = Path(path)
        if not p.exists():
            return cls([], [], activo=False)
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        dominios = [
            Dominio(id=did, capacidades=list(cfg.get("capacidades", [])),
                    aviso=(cfg.get("aviso") or "").strip())
            for did, cfg in (raw.get("dominios") or {}).items()
        ]
        urgencias = [
            Urgencia(id=u["id"],
                     patrones=[_norm(p) for p in u.get("patrones", [])],
                     respuesta=(u.get("respuesta") or "").strip(),
                     capacidades=list(u.get("capacidades", [])))
            for u in (raw.get("urgencias") or [])
        ]
        return cls(dominios, urgencias)

    # -- API ------------------------------------------------------------------
    def check(self, consulta: str, capacidades_agente: list[str]) -> GuardrailCheck:
        caps = set(capacidades_agente)
        dominios = [d.id for d in self._dominios if caps & set(d.capacidades)]
        avisos = [d.aviso for d in self._dominios if caps & set(d.capacidades) and d.aviso]

        q = _norm(consulta)
        for u in self._urgencias:
            if u.capacidades and not (caps & set(u.capacidades)):
                continue
            if any(p in q for p in u.patrones):
                return GuardrailCheck(escalate=True, urgencia_id=u.id,
                                      mensaje=u.respuesta, dominios=dominios, avisos=avisos)

        return GuardrailCheck(dominios=dominios, avisos=avisos)
