"""Proveedor fake que implementa el puerto `LLMProvider` de verdad.

A diferencia de `demo/fakes.py` —que simula el flujo entero para ilustrarlo—
este adaptador cumple la firma real `complete(req, resolved)` y se registra en
un `ProviderRegistry` como cualquier adaptador de producción. Así los tests
verifican el circuito real (perfil → registry → adaptador) en vez de una
maqueta paralela.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator

from providers.base import (
    Capability, LLMChunk, LLMProvider, LLMRequest, LLMResponse, ProviderError,
    ResolvedModel, Usage,
)


@dataclass
class RecordedCall:
    request: LLMRequest
    resolved: ResolvedModel


class FakeProvider(LLMProvider):
    """Devuelve texto predecible y registra cada llamada.

    `fail_with` permite forzar un `ProviderError` concreto (para probar la
    política de fallback): se lanza en las primeras `fail_times` llamadas y
    después el proveedor se comporta normalmente.
    """

    def __init__(self, name: str = "fake", *, text: str | Callable[[LLMRequest], str] | None = None,
                 fail_with: ProviderError | None = None, fail_times: int = 10**9,
                 delay_s: float = 0.0):
        self.name = name
        self._text = text
        self._fail_with = fail_with
        self._fail_times = fail_times
        self._delay_s = delay_s
        self.calls: list[RecordedCall] = []
        self._lock = threading.Lock()
        #: instantes (inicio, fin) de cada llamada — permite a un test verificar
        #: que dos llamadas se solaparon en el tiempo (paso 2.2: paralelización).
        self.call_windows: list[tuple[float, float]] = []

    def supports(self, cap: Capability) -> bool:
        return cap in {Capability.STREAMING}

    def complete(self, req: LLMRequest, resolved: ResolvedModel) -> LLMResponse:
        inicio = time.monotonic()
        with self._lock:
            self.calls.append(RecordedCall(req, resolved))
        if self._delay_s:
            time.sleep(self._delay_s)
        with self._lock:
            self.call_windows.append((inicio, time.monotonic()))
        if self._fail_with is not None and len(self.calls) <= self._fail_times:
            raise self._fail_with
        if callable(self._text):
            text = self._text(req)
        elif self._text is not None:
            text = self._text
        else:
            text = f"respuesta de {self.name} con {resolved.model}"
        return LLMResponse(text=text, model=resolved.model,
                           usage=Usage(input_tokens=10, output_tokens=5))

    def stream(self, req: LLMRequest, resolved: ResolvedModel) -> Iterator[LLMChunk]:
        yield LLMChunk(delta=self.complete(req, resolved).text)

    # -- ayudas de aserción ---------------------------------------------------
    @property
    def last(self) -> RecordedCall:
        if not self.calls:
            raise AssertionError(f"el proveedor '{self.name}' no recibió ninguna llamada")
        return self.calls[-1]

    @property
    def models_used(self) -> list[str]:
        return [c.resolved.model for c in self.calls]

    @property
    def profiles_requested(self) -> list[str]:
        return [c.request.profile for c in self.calls]


def citing_provider(name: str = "fake", prefijo: str = "respuesta generada") -> FakeProvider:
    """Fake que se comporta como un modelo obediente: cita TODA la evidencia.

    Devuelve el prefijo seguido de los marcadores `[N]` de cada bloque que
    recibió, que es exactamente lo que el prompt del motor pide. Sirve para
    probar el camino feliz del evaluador sin depender de un modelo real.
    """
    def _text(req: LLMRequest) -> str:
        evidencia = req.messages[-1].content
        marcas = re.findall(r"^\[(\d+)\]", evidencia, re.M)
        citas = " ".join(f"[{m}]" for m in marcas)
        return f"{prefijo} {citas}".strip()
    return FakeProvider(name, text=_text)


def hallucinating_provider(name: str = "fake") -> FakeProvider:
    """Fake que cita una fuente que nunca se le entregó."""
    return FakeProvider(name, text="La respuesta es X [informe-que-no-existe.md].")


def uncited_provider(name: str = "fake") -> FakeProvider:
    """Fake que responde sin citar nada."""
    return FakeProvider(name, text="La respuesta es X, confía en mí.")
