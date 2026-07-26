"""Ilustra las 2 afirmaciones estrella de Magnus, ejecutable.

⚠ ESTO NO ES LA SUITE DE VERIFICACIÓN — corre sobre las maquetas de
`demo/fakes.py`, no sobre `orchestration/engine.py`. Para verificar el runtime
real: `pytest`.


  P1/P2: añadir conocimiento MEJORA al agente sin tocar su definición.
  P5:    el proveedor de IA es INTERCAMBIABLE sin tocar la orquestación.

Ejecutar:  python demo/prove_principles.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import demo.fakes as fakes
from demo.fakes import (
    InMemoryRetriever, FakeEvaluator, FakeProvider, _Doc,
)
from providers.base import LLMRequest, Message, Role, LLMResponse
from kernel.rag.pipeline import RAGPipeline, RAGRequest

BAR = "═" * 66


def answer_amanda(provider) -> tuple[int, str]:
    """Amanda (productividad) responde. Su DEFINICIÓN nunca cambia."""
    rag = RAGPipeline(InMemoryRetriever(), InMemoryRetriever())
    ev = FakeEvaluator()
    q = "Quiero cambiar de trabajo; ¿cómo organizo la transición?"
    ctx = rag.build_context(RAGRequest(query=q, namespaces=["productivity/"],
                                       top_k=4, require_citations=True))
    resp = provider.complete(LLMRequest(
        messages=[Message(Role.SYSTEM, "Eres el agente amanda."),
                  Message(Role.USER, f"Pregunta: {q}\n\nEvidencia:\n{ctx.as_prompt_block()}")],
        profile="reasoning_high"))
    e = ev.evaluate(resp.text, len(ctx.citations), require_citations=True)
    return e.score, e.verdict, len(ctx.chunks)


# ── PRUEBA 1: añadir conocimiento mejora al agente ────────────────────────
print(BAR)
print("PRUEBA 1 · Añadir conocimiento mejora al agente (sin tocar su código)")
print(BAR)

score, verdict, n = answer_amanda(FakeProvider())
print(f"Antes:  amanda tiene {n} fragmento(s) → score={score} · veredicto={verdict}")

# Simulamos ingerir 2 documentos nuevos en knowledge/productivity/
# (en real: magnus knowledge ingest ...  → versionado + reindex)
fakes._SEED.extend([
    _Doc("prod-2", "productivity/",
         "Definir un presupuesto de tiempo semanal para la búsqueda evita el "
         "agotamiento y mantiene el foco.",
         "Magnus Productivity Guide v2", {"cambiar", "trabajo", "transicion", "objetivos", "foco"}),
    _Doc("prod-3", "productivity/",
         "Preparar el currículum y la red de contactos antes de renunciar reduce "
         "el tiempo de transición.",
         "Career Transitions Handbook", {"cambiar", "trabajo", "transicion", "red", "curriculum"}),
])

score2, verdict2, n2 = answer_amanda(FakeProvider())
print(f"Después: amanda tiene {n2} fragmento(s) → score={score2} · veredicto={verdict2}")
print(f"→ La MISMA amanda (0 cambios en agent.yaml) pasó de '{verdict}' a '{verdict2}'.\n")


# ── PRUEBA 2: el proveedor de IA es intercambiable ────────────────────────
print(BAR)
print("PRUEBA 2 · El modelo de IA es intercambiable (mismo puerto LLMProvider)")
print(BAR)


class OtherProvider:
    """Otro 'proveedor' (p.ej. Ollama local) — MISMO contrato .complete(req)."""
    def complete(self, req: LLMRequest) -> LLMResponse:
        n_ev = req.messages[-1].content.count("[")
        txt = (f"[respuesta generada por proveedor LOCAL] Basado en {n_ev} evidencias; "
               "recomendación conservadora. Confianza: media.")
        return LLMResponse(text=txt, model="ollama:qwen2.5")


for name, prov in [("FakeProvider (nube)", FakeProvider()), ("OtherProvider (local)", OtherProvider())]:
    s, v, _ = answer_amanda(prov)
    print(f"{name:<24} → responde OK, score={s} veredicto={v}")

print("\n→ Se cambió el proveedor SIN tocar Router, RAG, Evaluador ni agent.yaml.")
print("  En producción: editar configs/models.yaml (profile → provider).")
print(BAR)
