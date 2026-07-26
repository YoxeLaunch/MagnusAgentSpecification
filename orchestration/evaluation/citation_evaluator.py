"""Verificación PROGRAMÁTICA de citas (paso 3.2 del ROADMAP).

Empieza por lo estructural y verificable sin coste ni red: ¿la respuesta cita
fuentes que efectivamente se le entregaron? Es deliberadamente el escalón más
bajo — no juzga si el razonamiento es bueno, juzga si está anclado. El escalón
siguiente (LLM-as-judge contra `evaluation.rubric_ref`) se enchufa
implementando el mismo `Evaluator`, sin tocar el motor.

Dos fallos distintos, ambos graves y ambos detectables sin modelo:

  1. **Sin cita** — la respuesta no referencia ninguna de las fuentes dadas.
     No se puede distinguir de una respuesta inventada.
  2. **Cita fabricada** — la respuesta referencia una fuente o un índice que
     NO estaba en la evidencia. Es peor que no citar: aparenta fundamento.

El rigor del agente (`evaluation.rigor`, 1-10) sube el listón: a partir de 8 se
exigen dos fuentes distintas cuando hubo al menos dos disponibles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from kernel.rag.pipeline import RAGContext

# `[3]`, `[fuente.md]`, `[01-Finanzas/Nota.md · «Título»]`…
_MARCADOR = re.compile(r"\[([^\[\]]{1,300})\]")

_RIGOR_DOS_FUENTES = 8


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    score: float                    # 0.0 – 1.0
    reason: str
    cited: list[str] = field(default_factory=list)      # fuentes válidas citadas
    fabricated: list[str] = field(default_factory=list)  # marcadores sin respaldo
    required_sources: int = 1

    def as_dict(self) -> dict:
        return {
            "aprobada": self.passed,
            "score": round(self.score, 3),
            "razon": self.reason,
            "fuentes_citadas": self.cited,
            "citas_fabricadas": self.fabricated,
            "fuentes_exigidas": self.required_sources,
        }


class Evaluator(Protocol):
    """Puerto del evaluador: intercambiable como cualquier otro adaptador."""

    def evaluate(self, *, answer: str, ctx: RAGContext, rigor: int) -> EvaluationResult: ...


class CitationEvaluator:
    """Evaluador estructural de citas. Sin red, sin coste, determinista."""

    name = "citation_structural"

    def evaluate(self, *, answer: str, ctx: RAGContext, rigor: int = 5) -> EvaluationResult:
        disponibles = [c.provenance.source for c in ctx.chunks]
        exigidas = self._fuentes_exigidas(rigor, len(set(disponibles)))

        if not disponibles:
            return EvaluationResult(
                False, 0.0, "no se recuperó evidencia sobre la que citar",
                required_sources=exigidas)

        validas, fabricadas = self._clasificar(answer, disponibles)

        if fabricadas:
            return EvaluationResult(
                False, 0.0,
                f"la respuesta cita {len(fabricadas)} referencia(s) que no estaban "
                f"en la evidencia entregada: {fabricadas}",
                cited=sorted(validas), fabricated=fabricadas, required_sources=exigidas)

        if not validas:
            return EvaluationResult(
                False, 0.0,
                "la respuesta no cita ninguna de las fuentes recuperadas",
                required_sources=exigidas)

        score = min(1.0, len(validas) / exigidas)
        if len(validas) < exigidas:
            return EvaluationResult(
                False, score,
                f"se exigen {exigidas} fuentes distintas (rigor={rigor}) y solo "
                f"se citó {len(validas)}",
                cited=sorted(validas), required_sources=exigidas)

        return EvaluationResult(
            True, score, f"respuesta anclada en {len(validas)} fuente(s) recuperada(s)",
            cited=sorted(validas), required_sources=exigidas)

    # -- interno --------------------------------------------------------------
    @staticmethod
    def _fuentes_exigidas(rigor: int, disponibles: int) -> int:
        if rigor >= _RIGOR_DOS_FUENTES and disponibles >= 2:
            return 2
        return 1

    @staticmethod
    def _clasificar(answer: str, disponibles: list[str]) -> tuple[set[str], list[str]]:
        """Separa los marcadores `[...]` en citas válidas y fabricadas.

        Se aceptan dos formas, que son las dos que el prompt puede producir:
        el índice del bloque de evidencia (`[2]`) y el nombre de la fuente
        (completo o el nombre del archivo).
        """
        validas: set[str] = set()
        fabricadas: list[str] = []

        archivos = {src.split(" · ")[0]: src for src in disponibles}

        for bruto in _MARCADOR.findall(answer):
            marca = bruto.strip()
            if not marca:
                continue

            if marca.isdigit():
                i = int(marca)
                if 1 <= i <= len(disponibles):
                    validas.add(disponibles[i - 1])
                else:
                    fabricadas.append(marca)
                continue

            coincidencia = next(
                (src for archivo, src in archivos.items()
                 if archivo in marca or marca in src or _stem(archivo) in marca),
                None)
            if coincidencia is not None:
                validas.add(coincidencia)
            else:
                fabricadas.append(marca)

        return validas, fabricadas


def _stem(ruta: str) -> str:
    return ruta.rsplit("/", 1)[-1].removesuffix(".md")
