"""Demo ejecutable END-TO-END de Magnus Dynamic Group (sin red, sin claves).

⚠ ESTO NO ES LA SUITE DE VERIFICACIÓN — es ilustración. El flujo que ves aquí
corre sobre maquetas de `demo/fakes.py` (router, evaluador, proveedor y wiki
falsos), no sobre `orchestration/engine.py`, que es el único camino que usan
`mcp_server/` y `sdk/cli.py` en producción. Que esta demo pase no dice nada
sobre el runtime real. Para verificar el runtime: `pytest`.

Ejercita en un solo flujo: Router → Registro → RAG → Provider → Evaluador →
fusión, con traza de auditoría impresa. Demuestra el escenario multiagente:

  "Quiero cambiar de trabajo porque estoy estresado y además quiero invertir
   mi dinero."

Ejecutar:  python demo/run_demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# La consola de Windows suele usar cp1252; forzamos UTF-8 para la salida.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from providers.base import LLMRequest, Message, Role
from kernel.rag.pipeline import RAGPipeline, RAGRequest
from orchestration.router import Router, UserRequest, AgentAnswer
from demo.fakes import (
    FakeProvider, InMemoryRetriever, FakeRegistry, FakeAgent, FakeEvaluator,
)

BAR = "─" * 70


def log(step: str, msg: str) -> None:
    print(f"  [{step:<9}] {msg}")


def run_agent(agent_id: str, namespaces: list[str], request: UserRequest,
              rag: RAGPipeline, provider: FakeProvider, evaluator: FakeEvaluator) -> AgentAnswer:
    """Un agente: construye contexto por RAG (su parcela) → LLM → evaluación."""
    log("RAG", f"{agent_id}: recuperando en {namespaces}")
    ctx = rag.build_context(RAGRequest(query=request.message, namespaces=namespaces,
                                       top_k=4, require_citations=True))
    log("RAG", f"{agent_id}: {len(ctx.chunks)} fragmentos, "
               f"fuentes={[c.provenance.source for c in ctx.chunks]}")

    # el agente 'razona' SOLO sobre la evidencia recuperada (P1/P3)
    resp = provider.complete(LLMRequest(
        messages=[
            Message(Role.SYSTEM, f"Eres el agente {agent_id}. Responde solo con evidencia."),
            Message(Role.USER, f"Pregunta: {request.message}\n\nEvidencia:\n{ctx.as_prompt_block()}"),
        ],
        profile="reasoning_high",
    ))

    ev = evaluator.evaluate(resp.text, len(ctx.citations), require_citations=True)
    log("EVAL", f"{agent_id}: score={ev.score} veredicto={ev.verdict} ({ev.notes})")

    citations = [{"source": p.source, "version": p.knowledge_version} for p in ctx.citations]
    return AgentAnswer(agent_id=agent_id, text=resp.text, citations=citations, score=ev.score)


def main() -> None:
    # --- composición (en producción esto lo hace un contenedor de DI) --------
    provider = FakeProvider()
    retriever = InMemoryRetriever()
    rag = RAGPipeline(dense=retriever, lexical=retriever, reranker=None)
    evaluator = FakeEvaluator()
    registry = FakeRegistry([
        FakeAgent("ernesto_libras",
                  {"invertir", "inversion", "dinero", "riesgo", "economia", "finanzas"},
                  ["economics/", "finance/"]),
        FakeAgent("serena",
                  {"estres", "estresado", "trabajo", "salud", "burnout", "ansiedad"},
                  ["psychology/"]),
        FakeAgent("amanda",
                  {"cambiar", "trabajo", "productividad", "transicion", "objetivos"},
                  ["productivity/"]),
    ])
    router = Router(registry, provider, max_agents=3, min_score=0.3)

    # --- petición del usuario ------------------------------------------------
    request = UserRequest(
        request_id="req-001", user_id="u1",
        message="Quiero cambiar de trabajo porque estoy estresado y además quiero invertir mi dinero.")

    print(BAR)
    print("MAGNUS DYNAMIC GROUP — demo multiagente (adaptadores en memoria)")
    print(BAR)
    print(f'Usuario: "{request.message}"\n')

    # 1) ROUTER: intención → agentes
    plan = router.route(request)
    log("ROUTER", f"agentes={plan.agents} modo={plan.mode.value} · motivo: {plan.reason}")

    # 2) cada agente responde (aquí en paralelo lógico)
    print()
    answers = [
        run_agent(a, registry.namespaces(a), request, rag, provider, evaluator)
        for a in plan.agents
    ]

    # 3) solo publican los que superan la rúbrica
    published = [a for a in answers if (a.score or 0) >= 80]
    print()
    log("QUALITY", f"{len(published)}/{len(answers)} respuestas superan el umbral de calidad")

    # 4) ROUTER: fusión → informe único
    final = router.merge(request, published)

    print("\n" + BAR)
    print("RESPUESTA FINAL")
    print(BAR)
    print(final.text)
    print("\nContribuyeron:", ", ".join(final.contributors))
    print("Citas:", final.citations)
    print(BAR)
    print("Trazabilidad: cada afirmación cita fuente y knowledge_version. "
          "Ningún agente escribió en knowledge/ (P1/P3/P7).")


if __name__ == "__main__":
    main()
