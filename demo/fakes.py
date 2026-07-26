"""Adaptadores FALSOS / en memoria para las demos de `demo/`.

⚠ No los uses en tests: la suite de verificación vive en `tests/` y usa
`tests/magnus_fixtures/fake_provider.py`, que implementa la firma real del
puerto (`complete(req, resolved)`) y se enchufa a un `ProviderRegistry` de
verdad. Estos fakes están simplificados para que la demo se lea de un vistazo.


Permiten ejecutar el flujo multiagente completo sin red, sin claves y sin
vector store. Demuestran que la arquitectura de puertos-y-adaptadores funciona:
mañana sustituyes estos fakes por Anthropic + Qdrant sin tocar la orquestación.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from providers.base import LLMRequest, LLMResponse, Role, Usage
from kernel.rag.pipeline import Provenance, ScoredChunk
from orchestration.router import AgentMatch

# --------------------------------------------------------------------------
# 1) LLM FALSO — implementa el contrato del ProviderRegistry (.complete(req))
# --------------------------------------------------------------------------
class FakeProvider:
    """LLM determinista. Distingue 'modo agente' de 'modo editor (merge)'."""

    def complete(self, req: LLMRequest) -> LLMResponse:
        system = " ".join(m.content for m in req.messages if m.role == Role.SYSTEM).lower()
        user = " ".join(m.content for m in req.messages if m.role == Role.USER)

        if "editor jefe" in system:                     # modo fusión (Router.merge)
            return LLMResponse(text=self._merge(user), model="fake-editor")

        return LLMResponse(text=self._agent_answer(system, user), model="fake-agent")

    @staticmethod
    def _agent_answer(system: str, user: str) -> str:
        # extrae los fragmentos de evidencia [n] (Fuente) texto...
        cites = re.findall(r"\[(\d+)\] \(([^)]+)\)\s*([^\[]+)", user)
        if not cites:
            return ("No dispongo de evidencia recuperable en la LLM Wiki para esta "
                    "consulta. Nivel de confianza: bajo. Recomiendo ampliar `knowledge/`.")
        puntos = "\n".join(f"  - {txt.strip()} [{n}]" for n, _src, txt in cites[:3])
        confianza = "alta" if len(cites) >= 2 else "media"
        return (
            "Análisis basado únicamente en evidencia recuperada:\n"
            f"{puntos}\n"
            f"Conclusión: la evidencia respalda la respuesta. Nivel de confianza: {confianza}.\n"
            f"Fuentes: {', '.join(sorted({src for _n, src, _t in cites}))}."
        )

    @staticmethod
    def _merge(user: str) -> str:
        secciones = re.findall(r"### (\w[\w_]*)\n(.+?)(?=\n### |\Z)", user, re.S)
        out = ["INFORME UNIFICADO (fusionado por el editor jefe)\n"]
        for agente, cuerpo in secciones:
            out.append(f"■ {agente}\n{cuerpo.strip()}\n")
        out.append("Nota del editor: respuestas coherentes, sin contradicciones detectadas.")
        return "\n".join(out)


# --------------------------------------------------------------------------
# 2) BASE DE CONOCIMIENTO en memoria (semilla de la LLM Wiki)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Doc:
    chunk_id: str
    namespace: str
    text: str
    source: str
    keywords: set[str]


_SEED = [
    _Doc("eco-1", "economics/",
         "Diversificar la inversión reduce el riesgo no sistemático; un perfil "
         "conservador prioriza renta fija y liquidez.",
         "OECD Investing Basics 2025", {"invertir", "inversion", "dinero", "riesgo", "cartera"}),
    _Doc("fin-1", "finance/",
         "Antes de invertir conviene disponer de un fondo de emergencia de 3-6 "
         "meses de gastos.",
         "World Bank Financial Literacy 2024", {"invertir", "inversion", "dinero", "ahorro", "emergencia"}),
    _Doc("psy-1", "psychology/",
         "El estrés laboral sostenido se asocia a agotamiento (burnout); pausas "
         "y límites claros reducen su impacto.",
         "WHO Occupational Health 2023", {"estres", "estresado", "trabajo", "burnout", "salud"}),
    _Doc("psy-2", "psychology/",
         "Decidir un cambio de trabajo bajo estrés agudo tiende a sesgar la "
         "decisión; conviene estabilizar antes de decidir.",
         "APA Decision Making 2022", {"cambiar", "trabajo", "estres", "decision", "estresado"}),
    _Doc("prod-1", "productivity/",
         "Una transición laboral se gestiona mejor con objetivos por fases y un "
         "calendario realista de búsqueda.",
         "Magnus Productivity Guide", {"cambiar", "trabajo", "productividad", "transicion", "objetivos"}),
]


def _tokenize(text: str) -> set[str]:
    t = text.lower()
    t = (t.replace("á", "a").replace("é", "e").replace("í", "i")
          .replace("ó", "o").replace("ú", "u"))
    return set(re.findall(r"[a-z]+", t))


class InMemoryRetriever:
    """Sirve como retriever denso Y léxico (score por solape de términos)."""

    def retrieve(self, query: str, namespaces: list[str], k: int) -> list[ScoredChunk]:
        q = _tokenize(query)
        out: list[ScoredChunk] = []
        for d in _SEED:
            if not any(d.namespace.startswith(ns) or ns.startswith(d.namespace)
                       for ns in namespaces):
                continue
            overlap = len(q & (d.keywords | _tokenize(d.text)))
            if overlap == 0:
                continue
            score = min(1.0, 0.25 + 0.15 * overlap)
            out.append(ScoredChunk(
                chunk_id=d.chunk_id, text=d.text, score=score, namespace=d.namespace,
                provenance=Provenance(source=d.source, date="2025", knowledge_version="v1")))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:k]


# --------------------------------------------------------------------------
# 3) REGISTRO de agentes en memoria (búsqueda semántica simplificada)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FakeAgent:
    agent_id: str
    domains_kw: set[str]
    namespaces: list[str]


class FakeRegistry:
    def __init__(self, agents: list[FakeAgent]):
        self._agents = {a.agent_id: a for a in agents}

    def search(self, intent: str, k: int) -> list[AgentMatch]:
        q = _tokenize(intent)
        matches = []
        for a in self._agents.values():
            overlap = len(q & a.domains_kw)
            if overlap:
                matches.append(AgentMatch(a.agent_id, min(1.0, 0.3 + 0.2 * overlap)))
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:k]

    def namespaces(self, agent_id: str) -> list[str]:
        return self._agents[agent_id].namespaces


# --------------------------------------------------------------------------
# 4) EVALUADOR falso (aplica la rúbrica: exige citas)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Evaluation:
    score: int
    verdict: str            # publish | retry | escalate
    notes: str


class FakeEvaluator:
    def evaluate(self, answer_text: str, n_citations: int, require_citations: bool) -> Evaluation:
        if require_citations and n_citations == 0:
            return Evaluation(0, "retry", "Sin citas y require_citations=true → rechazo.")
        score = min(100, 60 + 12 * n_citations)
        verdict = "publish" if score >= 80 else "escalate"
        return Evaluation(score, verdict, f"{n_citations} citas verificadas.")
