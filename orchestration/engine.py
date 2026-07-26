"""Motor Magnus reutilizable: Agent Registry → Capability Engine →
RAG(wiki real) → ProviderRegistry → adaptador → fusión. Lo consumen tanto el
servidor MCP como la CLI/API.

Reconciliación (docs/04-MAGNUS-V2-ARQUITECTURA.md): ningún agente vive
hardcodeado aquí. La única fuente de verdad es `agents/*/agent.yaml`
(cargada por `AgentRegistry`) y `capabilities/*.capability.yaml` (cargada por
`CapabilityCatalog`, consumida por `CapabilityEngine`). Antes de esta
versión, 4 de los 5 agentes y el enrutado por keywords vivían como
estructuras de datos en este archivo — esa era exactamente la brecha que la
auditoría detectó entre documentación y runtime.

La misma regla aplica a la configuración de modelo y recuperación: el motor NO
decide con qué modelo ni con qué umbral responde un agente. Lo dice su
`agent.yaml` y lo resuelve `ProviderRegistry` contra `configs/models.yaml`
(paso 2 del ROADMAP). Antes de esta versión el motor forzaba
`profile="local_private"` y `min_score` global, ignorando ambos.

Diseñado para funcionar SIN LLM (modo extractivo con fundamento) y para usar
proveedores reales (Ollama/Anthropic) en cuanto se configuren — sin cambiar
la orquestación.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from kernel.rag.file_store import FileWikiStore
from kernel.rag.pipeline import RAGPipeline, RAGRequest, RAGContext
from kernel.rag.vector_store import InMemoryVectorStore
from providers.base import LLMRequest, Message, ProviderError, Role
from providers.registry import ProviderRegistry, ProviderTrace

from .audit import NullTraceStore, TraceStore, build_entry
from .evaluation import CitationEvaluator, EvaluationResult, Evaluator, Guardrails
from .permissions import PermissionEngine
from .privacy import EgressPolicy
from .registry.agent_registry import AgentRegistry, AgentSpec, LoadReport
from .registry.capability_catalog import CapabilityCatalog
from .capability_engine import CapabilityEngine

log = logging.getLogger("magnus.engine")

# Techo del bloque de evidencia que se manda al proveedor. Acota coste y
# latencia, y evita que un top_k generoso desborde la ventana de contexto.
MAX_CONTEXT_CHARS = 12_000

# Score mínimo de capacidad para considerar que una consulta tiene dominio
# identificado. Por debajo, el motor responde que no lo identificó en vez de
# elegir un agente arbitrario (ver `MagnusEngine._route`).
ROUTING_MIN_SCORE = 0.35


@dataclass
class AgentResult:
    agent_id: str
    answer: str
    sources: list[str] = field(default_factory=list)
    n_chunks: int = 0
    trace: dict = field(default_factory=dict)


class MagnusEngine:
    """Orquesta una consulta de punta a punta.

    Args:
        root: raíz del proyecto (contiene `agents/`, `capabilities/`, `configs/`).
        provider: atajo — un adaptador suelto. Se envuelve en un
            `ProviderRegistry` construido desde `configs/models.yaml`, así que
            solo servirán los perfiles que resuelvan a ese proveedor; el resto
            degradará explícitamente.
        providers: el `ProviderRegistry` completo. Es la vía recomendada.
        top_k / min_score: valores por DEFECTO, usados solo cuando el agente no
            declara los suyos en `knowledge.retrieval`.
        on_provider_error: `"degrade"` (por defecto) responde de forma
            extractiva declarando el fallo; `"raise"` propaga el error
            normalizado. Nunca se devuelve una respuesta débil en silencio.
        evaluator: verifica que la respuesta esté anclada en la evidencia. Por
            defecto `CitationEvaluator` (estructural, sin coste).
        guardrails: límites por dominio y escalado por urgencia. Por defecto se
            cargan de `configs/guardrails.yaml`.
        trace_store: registro auditable. Por defecto NO escribe nada — la wiki
            contiene datos personales y el registro es opt-in (ver
            `orchestration/audit.py`).
        hybrid: recuperación léxica + vectorial local (por defecto). `False`
            deja solo el retriever léxico — útil para medir el baseline en el
            banco de recuperación, no para producción.
        routing_min_score: umbral de capacidad por debajo del cual la consulta
            se considera sin dominio identificado y no se enruta a ningún
            agente. Bajarlo hace que el motor adivine; subirlo lo hace más
            reticente a responder.
    """

    def __init__(self, root: str | Path, provider=None, *,
                 providers: ProviderRegistry | None = None,
                 top_k: int = 5, min_score: float = 0.02,
                 max_context_chars: int = MAX_CONTEXT_CHARS,
                 on_provider_error: str = "degrade",
                 evaluator: Evaluator | None = None,
                 guardrails: Guardrails | None = None,
                 trace_store: TraceStore | None = None,
                 hybrid: bool = True,
                 permissions: PermissionEngine | None = None,
                 egress: EgressPolicy | None = None,
                 caller_role: str = "operator",
                 routing_min_score: float = ROUTING_MIN_SCORE):
        self.root = Path(root)
        self.top_k = top_k
        self.min_score = min_score
        self.max_context_chars = max_context_chars
        if on_provider_error not in ("degrade", "raise"):
            raise ValueError("on_provider_error debe ser 'degrade' o 'raise'")
        self.on_provider_error = on_provider_error
        self.evaluator = evaluator if evaluator is not None else CitationEvaluator()
        self.guardrails = guardrails if guardrails is not None else Guardrails.from_yaml(
            self.root / "configs" / "guardrails.yaml")
        self.trace_store = trace_store if trace_store is not None else NullTraceStore()
        self.permissions = permissions if permissions is not None else PermissionEngine.from_yaml(
            self.root / "configs" / "permissions.yaml")
        self.egress = egress if egress is not None else EgressPolicy.from_yaml(
            self.root / "configs" / "privacy.yaml")
        self.caller_role = caller_role
        self.routing_min_score = routing_min_score

        self.capabilities = CapabilityCatalog(self.root / "capabilities").load_all()
        self.registry = AgentRegistry(
            self.root / "agents",
            capabilities=self.capabilities,
            models_yaml=self.root / "configs" / "models.yaml",
            permissions_yaml=self.root / "configs" / "permissions.yaml",
            mcp_catalog_yaml=self.root / "tools" / "mcp_catalog.yaml",
        )
        self._load_report: LoadReport = self.registry.load_all()
        self.capability_engine = CapabilityEngine(self.capabilities, self.registry)

        self.providers = providers if providers is not None else _envolver(provider, self.root)

        wiki_dir = self.root / "LLM-Wiki" / "wiki"
        if not wiki_dir.exists() and (self.root / "LLM-Wiki.example" / "wiki").exists():
            wiki_dir = self.root / "LLM-Wiki.example" / "wiki"

        self.store = FileWikiStore(wiki_dir)
        self._ingest_report = self.store.ingest()

        # Recuperación híbrida REAL: léxica (`FileWikiStore`) + vectorial local
        # (`InMemoryVectorStore` con random indexing/TF-IDF, no embeddings
        # neuronales), dos implementaciones distintas sobre los mismos chunks.
        # Antes ambos argumentos eran el mismo `FileWikiStore`, así que el
        # pipeline decía "híbrido" haciendo dos veces lo léxico.
        if hybrid:
            self.vectors = InMemoryVectorStore.from_wiki_store(self.store)
            self._ingest_report["vectorial"] = {
                "chunks": len(self.vectors), "dim": self.vectors.embedder.dim,
                "embedder": self.vectors.embedder.name}
            self.rag = RAGPipeline(self.vectors, self.store)
        else:
            self.vectors = None
            self.rag = RAGPipeline(self.store, self.store)

    # -- diagnóstico ------------------------------------------------------
    @property
    def ingest_report(self) -> dict:
        return self._ingest_report

    @property
    def load_report(self) -> LoadReport:
        """Qué agentes cargaron y cuáles fallaron validación (con errores)."""
        return self._load_report

    def list_agents(self) -> list[dict]:
        return [{"id": a.id, "nombre": a.name, "namespaces": a.knowledge_sources}
                for a in self.registry.list(status="active")]

    # -- enrutado (delegado al Capability Engine, sin keywords en código) --
    def _route(self, message: str) -> list[AgentSpec]:
        """Agentes cuya capacidad alcanza el umbral, o lista vacía.

        Devolver `[]` es una respuesta legítima y es lo que el llamante debe
        manejar. Antes, cuando ninguna capacidad llegaba al umbral, se caía en
        `registry.list(status="active")[:1]` — el primer agente activo por
        orden alfabético. Eso no es "degradar con gracia": es contestar una
        consulta de salud con el agente de finanzas porque su `id` empieza por
        'a', recuperar de la parcela equivocada y presentar el resultado con la
        misma apariencia de fundamento que una respuesta bien enrutada. Un
        enrutado incorrecto silencioso es peor que no responder.
        """
        return self.capability_engine.route_to_agents(
            message, k=3, min_score=self.routing_min_score)

    # -- consulta principal ----------------------------------------------
    def ask(self, message: str, agent_id: str | None = None) -> dict:
        agents = [self.registry.get(agent_id)] if agent_id else self._route(message)

        # Señales de urgencia: se comprueban ANTES de recuperar evidencia o
        # llamar a ningún modelo. Ante una de ellas la respuesta correcta no es
        # una respuesta mejor documentada, es una vía de contacto humano.
        capacidades = [c.id for a in agents for c in a.capabilities]
        urgencia = self.guardrails.check(message, capacidades)
        if urgencia.escalate:
            return self._escalar(message, agents, urgencia)

        # Se comprueba DESPUÉS de la urgencia a propósito: una señal de crisis
        # debe escalar aunque el enrutado no haya identificado ningún dominio.
        if not agents:
            return self._sin_dominio(message)

        results: list[AgentResult] = []
        for a in agents:
            check = self.guardrails.check(message, [c.id for c in a.capabilities])

            # Enforcement de permisos ANTES de recuperar: la parcela efectiva
            # es la intersección de knowledge.sources y la política del agente,
            # no lo que el agente declara por su cuenta.
            namespaces = self.permissions.allowed_namespaces(a)
            if not namespaces:
                results.append(self._denegado_por_permisos(a, message, check))
                continue

            ctx = self.rag.build_context(RAGRequest(
                query=message, namespaces=namespaces,
                top_k=a.top_k or self.top_k,
                # el umbral lo declara el agente en knowledge.retrieval.min_score
                min_score=a.min_score if a.min_score is not None else self.min_score,
                # y la exigencia de citas, en evaluation.require_citations
                require_citations=a.require_citations))
            results.append(self._agent_answer(a, message, ctx, check))

        merged = self._merge(message, results)
        return {
            "respuesta": merged,
            "agentes": [r.agent_id for r in results],
            "fuentes": sorted({s for r in results for s in r.sources}),
            "traza": {r.agent_id: r.trace for r in results},
        }

    def _sin_dominio(self, message: str) -> dict:
        """Ninguna capacidad alcanzó el umbral: se dice, no se improvisa."""
        disponibles = self.registry.list(status="active")
        cercanas = self.capability_engine.match(message, k=3, min_score=0.0)
        log.info("consulta sin dominio identificado (umbral %.2f); capacidades más "
                 "cercanas: %s", self.routing_min_score,
                 [(m.capability_id, round(m.score, 3)) for m in cercanas])

        catalogo = "\n".join(f"  · {a.id} — {a.name}: {', '.join(a.knowledge_sources)}"
                             for a in disponibles)
        respuesta = (
            "No identifiqué ningún dominio de conocimiento con confianza suficiente "
            "para esta consulta, así que no la voy a responder: hacerlo con un agente "
            "elegido arbitrariamente daría una respuesta buscada en la parcela "
            "equivocada de tu wiki, con la misma apariencia de fundamento que una "
            "bien enrutada.\n\n"
            "Puedes reformularla con más contexto del tema, o dirigirla a un agente "
            "concreto por su id:\n" + (catalogo or "  (no hay agentes activos)"))

        traza = {
            "modo": "sin_dominio",
            "umbral_enrutado": self.routing_min_score,
            "capacidades_mas_cercanas": [
                {"capacidad": m.capability_id, "score": round(m.score, 3), "via": m.via}
                for m in cercanas],
            "agentes_activos": [a.id for a in disponibles],
        }
        self.trace_store.record(build_entry(
            agent_id="", question=message, chunks=[], evaluation=None,
            guardrails=None, provider_trace={}, mode="sin_dominio"))
        return {"respuesta": respuesta, "agentes": [], "fuentes": [],
                "traza": {"_enrutado": traza}}

    def _escalar(self, message: str, agents: list[AgentSpec], urgencia) -> dict:
        log.warning("consulta escalada por guardrail '%s'", urgencia.urgencia_id)
        self.trace_store.record(build_entry(
            agent_id=",".join(a.id for a in agents), question=message, chunks=[],
            evaluation=None, guardrails=urgencia.as_dict(), provider_trace={},
            mode="escalado_urgencia"))
        return {
            "respuesta": urgencia.mensaje,
            "agentes": [],
            "fuentes": [],
            "traza": {"_guardrails": {"modo": "escalado_urgencia",
                                      **urgencia.as_dict()}},
        }

    def _denegado_por_permisos(self, a: AgentSpec, message: str, check) -> AgentResult:
        policy = self.permissions.policy_for(a)
        motivo = (f"la política '{policy.id}' no concede lectura sobre ninguno de "
                  f"los namespaces que {a.id} declara ({a.knowledge_sources})")
        log.error("consulta denegada — %s", motivo)
        resultado = AgentResult(
            a.id, f"[{a.name}] No puedo consultar mi base de conocimiento: {motivo}.",
            [], 0, {"modo": "denegado_por_permisos", "politica": policy.id,
                    "namespaces_declarados": a.knowledge_sources,
                    "guardrails": check.as_dict()})
        self.trace_store.record(build_entry(
            agent_id=a.id, question=message, chunks=[], evaluation=None,
            guardrails=check.as_dict(), provider_trace={},
            mode="denegado_por_permisos"))
        return resultado

    def _agent_answer(self, a: AgentSpec, message: str, ctx: RAGContext,
                      check) -> AgentResult:
        sources = [c.provenance.source for c in ctx.chunks]
        base_trace = {
            "perfil_solicitado": a.model_profile,
            "fallback_profile": a.fallback_profile,
            "min_score": a.min_score,
            "top_k": a.top_k,
            "require_citations": a.require_citations,
            "rigor": a.rigor,
            "chunks_recuperados": len(ctx.chunks),
            "guardrails": check.as_dict(),
        }
        if not ctx.chunks:
            # Incluye el caso `require_citations=True` sin evidencia: el
            # pipeline ya devolvió contexto vacío en vez de dejar improvisar.
            razon = ("no hay evidencia suficiente y este agente exige citas"
                     if a.require_citations else "sin evidencia en su parcela de la wiki")
            return self._finalizar(
                a, message, ctx, AgentResult(
                    a.id, f"[{a.name}] ({razon})", [], 0,
                    {**base_trace, "modo": "sin_evidencia"}),
                check, evaluacion=None)

        # Política de egreso: ¿pueden estos pasajes salir del dispositivo?
        # Se decide sobre los namespaces REALMENTE recuperados, no sobre los
        # que el agente declara — puede haber recuperado de una parcela más
        # estrecha de la que tiene autorizada.
        egreso = self.egress.check(sorted({c.namespace for c in ctx.chunks}))
        base_trace["egreso"] = egreso.as_dict()

        motivo = self._por_que_no_hay_llm(a, only_local=not egreso.remote_allowed)
        if motivo is not None and not egreso.remote_allowed:
            motivo = (f"{motivo}; además {egreso.reason}, así que no se puede "
                      f"recurrir a un proveedor remoto")
        if motivo is not None:
            # El modo extractivo cita pasajes literales: está anclado por
            # construcción, no hay nada que evaluar.
            return self._finalizar(
                a, message, ctx,
                AgentResult(a.id, self._extractivo(a, ctx), sources, len(ctx.chunks),
                            {**base_trace, "modo": "extractivo", "motivo": motivo}),
                check, evaluacion=None)

        req = LLMRequest(
            messages=[
                Message(Role.SYSTEM, self._system_prompt(a, check)),
                Message(Role.USER,
                        f"Pregunta: {message}\n\nEvidencia:\n"
                        f"{ctx.as_prompt_block(self.max_context_chars)}"),
            ],
            profile=a.model_profile,          # ← del agente, nunca hardcodeado
            effort=a.effort,
        )
        try:
            resp, trace = self.providers.complete_with_trace(
                req, fallback_profile=a.fallback_profile,
                only_local=not egreso.remote_allowed)
        except ProviderError as e:
            return self._finalizar(
                a, message, ctx,
                self._fallo_de_proveedor(a, ctx, sources, base_trace, e),
                check, evaluacion=None)

        self._log_trace(a, trace)
        evaluacion = self.evaluator.evaluate(answer=resp.text, ctx=ctx, rigor=a.rigor)
        texto, modo = self._aplicar_politica(a, resp.text, ctx, evaluacion)
        resultado = AgentResult(
            a.id, texto, sources, len(ctx.chunks),
            {**base_trace, "modo": modo, **trace.as_dict(),
             "evaluacion": evaluacion.as_dict()})
        return self._finalizar(a, message, ctx, resultado, check, evaluacion)

    @staticmethod
    def _system_prompt(a: AgentSpec, check) -> str:
        partes = [f"Eres {a.name}. Responde SOLO con la evidencia dada, "
                  "en español, citando la fuente entre corchetes."]
        # Los límites del dominio entran en el prompt, además de anexarse a la
        # respuesta: es más barato que el modelo no cruce la línea a corregirlo
        # después.
        partes.extend(check.avisos)
        return " ".join(partes)

    # -- política ante una evaluación fallida (paso 3.3) --------------------
    def _aplicar_politica(self, a: AgentSpec, texto: str, ctx: RAGContext,
                          ev: EvaluationResult) -> tuple[str, str]:
        """Qué se responde cuando la evaluación no pasa.

        Nunca se devuelve la respuesta débil en silencio:

          - agente con `require_citations: true` → se RECHAZA la respuesta
            generada y se declara la incertidumbre, ofreciendo en su lugar los
            pasajes literales de la wiki (que sí están anclados). En finanzas,
            salud y salud mental una afirmación sin evidencia verificable no es
            un defecto de calidad, es un riesgo.
          - agente sin esa exigencia → la respuesta se conserva pero se marca
            de forma visible, no solo en la traza.
        """
        if ev.passed:
            return f"[{a.name}] {texto}", "llm"

        if a.require_citations:
            pasajes = self._extractivo(a, ctx).split("\n", 1)[1]
            return (f"[{a.name}] No puedo dar esta respuesta con la confianza que exige "
                    f"mi configuración: lo generado no quedó anclado en tu wiki "
                    f"({ev.reason}). Lo que sí dicen tus notas sobre el tema:\n{pasajes}",
                    "rechazada_por_evaluacion")

        return (f"[{a.name}] {texto}\n"
                f"⚠ Respuesta sin cita verificable a tu wiki ({ev.reason}).",
                "llm_con_advertencia")

    # -- cierre común: avisos de dominio + registro auditable ---------------
    def _finalizar(self, a: AgentSpec, message: str, ctx: RAGContext,
                   resultado: AgentResult, check, evaluacion) -> AgentResult:
        if check.avisos and resultado.n_chunks > 0:
            resultado.answer += "\n\n" + "\n".join(f"— {av}" for av in check.avisos)
        self.trace_store.record(build_entry(
            agent_id=a.id, question=message, chunks=ctx.chunks,
            evaluation=evaluacion.as_dict() if evaluacion is not None else None,
            guardrails=check.as_dict(),
            provider_trace={k: v for k, v in resultado.trace.items()
                            if k in ("perfil_solicitado", "proveedor_final",
                                     "modelo_final", "fallback_aplicado")},
            mode=resultado.trace.get("modo", "desconocido")))
        return resultado

    # -- proveedor: disponibilidad, degradación y traza --------------------
    def _por_que_no_hay_llm(self, a: AgentSpec, *, only_local: bool = False) -> str | None:
        """Motivo declarado por el que este agente no puede usar un LLM, o None."""
        if self.providers is None:
            return "no hay ningún proveedor configurado (modo extractivo)"
        disponible = self.providers.is_available(a.model_profile, only_local=only_local) or (
            a.fallback_profile
            and self.providers.is_available(a.fallback_profile, only_local=only_local))
        if not disponible:
            ambito = "local " if only_local else ""
            return (f"ningún adaptador {ambito}disponible para el perfil '{a.model_profile}'"
                    + (f" ni para '{a.fallback_profile}'" if a.fallback_profile else "")
                    + f" (adaptadores cargados: {self.providers.adapters() or 'ninguno'})")
        return None

    def _extractivo(self, a: AgentSpec, ctx: RAGContext) -> str:
        """MODO EXTRACTIVO: cita pasajes REALES de la wiki (sin LLM, sin coste)."""
        bullets = "\n".join(f"  • {c.text[:220].strip()}…\n    ↳ {c.provenance.source}"
                            for c in ctx.chunks[:3])
        return f"[{a.name}] Según tu wiki:\n{bullets}"

    def _fallo_de_proveedor(self, a: AgentSpec, ctx: RAGContext, sources: list[str],
                            base_trace: dict, e: ProviderError) -> AgentResult:
        log.warning("agente %s: fallo de proveedor (%s): %s", a.id, type(e).__name__, e)
        if self.on_provider_error == "raise":
            raise e
        # Degradación EXPLÍCITA: se responde con la evidencia real, pero se
        # declara que la generación falló. Nunca se disfraza de éxito.
        aviso = (f"[{a.name}] ⚠ El proveedor de IA falló ({e}). "
                 f"Respuesta degradada a extracción directa de tu wiki:")
        cuerpo = self._extractivo(a, ctx).split("\n", 1)[1]
        return AgentResult(a.id, f"{aviso}\n{cuerpo}", sources, len(ctx.chunks),
                           {**base_trace, "modo": "extractivo_degradado",
                            "error": str(e), "reintentable": e.retryable})

    @staticmethod
    def _log_trace(a: AgentSpec, trace: ProviderTrace) -> None:
        d = trace.as_dict()
        log.info("agente=%s perfil=%s → %s/%s fallback=%s latencia=%sms",
                 a.id, d["perfil_solicitado"], d["proveedor_final"], d["modelo_final"],
                 d["fallback_aplicado"], d["latencia_total_ms"])

    def _merge(self, message: str, results: list[AgentResult]) -> str:
        parts = [r.answer for r in results if r.n_chunks > 0]
        if not parts:
            if not results:
                return "No encontré evidencia en tu wiki para esa consulta."
            # Ningún agente encontró evidencia. Se devuelven sus declaraciones
            # en vez de un mensaje genérico: no es lo mismo "tu wiki no habla
            # de esto" que "hay algo pero este agente exige citas y no llega".
            return "\n\n".join(r.answer for r in results)
        if len(parts) == 1:
            return parts[0]
        return "INFORME UNIFICADO\n\n" + "\n\n".join(parts)


def _envolver(provider, root: Path) -> ProviderRegistry | None:
    """Compatibilidad: un adaptador suelto se envuelve en un ProviderRegistry.

    Así el motor tiene UN solo camino (siempre pasa por el registro y su
    política de fallback) aunque el llamante solo tenga un adaptador a mano.
    """
    if provider is None:
        return None
    models_yaml = root / "configs" / "models.yaml"
    if not models_yaml.exists():
        return None
    return ProviderRegistry.from_yaml(str(models_yaml), {provider.name: provider})
