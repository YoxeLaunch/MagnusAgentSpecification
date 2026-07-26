"""Capability Matcher — el ÚNICO componente que sabe responder
"¿de qué trata esta consulta?" en términos de `capabilities/*.yaml`.

Separación deliberada (a pedido explícito, antes de tocar embeddings):
este módulo NUNCA sabe qué es un agente. Solo conoce `CapabilityCatalog`
(taxonomía, relaciones, sinónimos) y devuelve `CapabilityMatch`. Quién
atiende esa capacidad es responsabilidad exclusiva de
`orchestration/capability/selection.py::AgentSelectionEngine`.

Consecuencia práctica de esta separación: pasar a embeddings mañana es
reemplazar `LexicalCapabilityMatcher` por un `EmbeddingCapabilityMatcher`
que implemente el mismo `CapabilityMatcher` (Protocol) — cero cambios en
`AgentSelectionEngine`, en `AgentRegistry` ni en `orchestration/engine.py`.

`LexicalCapabilityMatcher` ya es "consciente de taxonomía": no es un simple
solape de tokens por capacidad aislada. El score léxico crudo de una
capacidad se propaga:
  - hacia sus ANCESTROS (con decaimiento por nivel) — si la consulta
    matchea fuerte con `digestive_diseases`, `gastroenterology` y
    `medicine` también deben subir, porque un agente que declara la
    capacidad más general igual debe poder atenderla.
  - hacia sus RELACIONADOS declarados en `related` (con un peso fijo,
    menor que la propagación jerárquica) — p. ej. un match fuerte en
    `risk` sube un poco a `markets` y `macroeconomics` aunque no haya
    relación de jerarquía entre ellas.
Esto es exactamente el tipo de "arquitectura conceptual" (taxonomía +
relaciones + vocabulario) que hace que sustituir el texto-plano por
embeddings sea un cambio de implementación, no de diseño: un
`EmbeddingCapabilityMatcher` real seguiría querien hacer esta misma
propagación sobre el grafo de capacidades antes de devolver `match()`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Protocol

from kernel.rag.embedder import HashingEmbedder, coseno
from orchestration.registry.capability_catalog import Capability, CapabilityCatalog

_STOP = {
    "de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "que", "con",
    "por", "para", "del", "al", "se", "su", "sus", "o", "es", "como", "mas",
    "mi", "tu", "yo", "me", "te", "le", "lo", "no", "si", "tengo", "quiero",
    "the", "of", "to", "and", "in", "for", "on", "is", "are",
}

_ANCESTOR_DECAY = 0.5   # score que sube a cada nivel de ancestro se reduce a la mitad por nivel
_RELATED_WEIGHT = 0.3   # fracción fija que se propaga a una capacidad `related`


def _norm(text: str) -> str:
    t = text.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        t = t.replace(a, b)
    return t


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _norm(text)) if w not in _STOP and len(w) > 2}


@dataclass(frozen=True)
class CapabilityMatch:
    capability_id: str
    score: float
    via: str = "direct"   # "direct" | "ancestor" | "related" (auditable, no caja negra)


class CapabilityMatcher(Protocol):
    """Puerto: query → capacidades. Implementación intercambiable (léxica
    hoy, embeddings después) sin tocar Agent Selection Engine."""

    def match(self, query: str, *, k: int = 5, min_score: float = 0.35) -> list[CapabilityMatch]: ...
    def explain(self, query: str, capability_id: str) -> CapabilityMatch | None: ...


class LexicalCapabilityMatcher:
    """El vocabulario de dominio (description + routing_examples +
    synonyms) se pesa por IDF: un token que aparece en el índice de una
    sola capacidad (p. ej. 'estomago', 'gastritis') pesa mucho más que uno
    que aparece en varias (p. ej. 'riesgo', que aparece en 'risk',
    'finance' y 'technology_strategy'). Sin esto, una sola palabra genérica
    compartida entre capacidades no relacionadas produce falsos positivos;
    con esto, una palabra rara y específica del dominio (el "vocabulario
    por dominio" que se pidió antes de pasar a embeddings) es suficiente
    señal por sí sola. Es TF-IDF clásico, no embeddings — pero ya resuelve
    la mayoría de lo que un modelo semántico resolvería aquí, y el punto de
    extensión (`CapabilityMatcher`) sigue intacto para cuando se necesite.
    """

    def __init__(self, catalog: CapabilityCatalog):
        self._catalog = catalog
        self._index: dict[str, set[str]] = {}
        self._idf: dict[str, float] = {}
        self._cache: dict[tuple, list[CapabilityMatch]] = {}
        self._build_index()

    def _build_index(self) -> None:
        for cap in self._catalog.all():
            seed = " ".join([cap.description, *cap.routing_examples, *cap.synonyms])
            self._index[cap.id] = _tokens(seed)

        n_caps = max(1, len(self._index))
        doc_freq: dict[str, int] = {}
        for tokens in self._index.values():
            for tok in tokens:
                doc_freq[tok] = doc_freq.get(tok, 0) + 1
        self._idf = {
            tok: math.log((1 + n_caps) / (1 + df)) + 1.0
            for tok, df in doc_freq.items()
        }

    def _weight(self, token: str) -> float:
        return self._idf.get(token, 1.0)

    def _raw_scores(self, query: str) -> dict[str, float]:
        q = _tokens(query)
        if not q:
            return {}
        raw: dict[str, float] = {}
        for cap_id, cap_tokens in self._index.items():
            overlap = q & cap_tokens
            if not overlap:
                continue
            overlap_weight = sum(self._weight(t) for t in overlap)
            cap_weight = sum(self._weight(t) for t in cap_tokens) or 1.0
            raw[cap_id] = min(1.0, overlap_weight / (cap_weight ** 0.5))
        return raw

    def _propagate(self, raw: dict[str, float]) -> dict[str, tuple[float, str]]:
        """Devuelve {capability_id: (score, via)} combinando directo +
        propagación jerárquica + propagación por relacionados.

        Las contribuciones se ACUMULAN (suma, luego cap a 1.0), no se
        toma el máximo: si dos señales débiles distintas (p. ej. un
        sinónimo propio + un hijo de la jerarquía que también matcheó)
        apuntan a la misma capacidad, deben reforzarse — es exactamente el
        caso "me duele el estómago", donde 'duele' matchea `medicine`
        directo y 'estómago' matchea `gastroenterology` (hijo), y ninguna
        de las dos señales sola alcanza el umbral pero juntas sí deberían.
        `via` se queda con la fuente de mayor aporte individual, solo para
        que `explain()` sea legible — no afecta el score.
        """
        totals: dict[str, float] = dict(raw)
        via: dict[str, str] = {cid: "direct" for cid in raw}

        def _accumulate(cap_id: str, amount: float, source: str) -> None:
            prev = totals.get(cap_id, 0.0)
            totals[cap_id] = prev + amount
            if amount > raw.get(cap_id, -1.0) and via.get(cap_id) != "direct":
                via[cap_id] = source
            via.setdefault(cap_id, source)

        for cap_id, base in raw.items():
            cap = self._catalog.get(cap_id)
            if cap is None:
                continue
            for depth, ancestor_id in enumerate(self._catalog.ancestors_of(cap_id), start=1):
                _accumulate(ancestor_id, base * (_ANCESTOR_DECAY ** depth), "ancestor")
            for rel_id in cap.related:
                _accumulate(rel_id, base * _RELATED_WEIGHT, "related")

        return {cid: (min(1.0, s), via.get(cid, "direct")) for cid, s in totals.items()}

    def match(self, query: str, *, k: int = 5, min_score: float = 0.35) -> list[CapabilityMatch]:
        cache_key = (query, k, min_score)
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = self._raw_scores(query)
        combined = self._propagate(raw)
        result = [CapabilityMatch(cid, round(min(1.0, s), 3), via)
                  for cid, (s, via) in combined.items() if s >= min_score]
        result.sort(key=lambda m: m.score, reverse=True)
        result = result[:k]
        self._cache[cache_key] = result
        return result

    def explain(self, query: str, capability_id: str) -> CapabilityMatch | None:
        raw = self._raw_scores(query)
        combined = self._propagate(raw)
        if capability_id not in combined:
            return None
        score, via = combined[capability_id]
        return CapabilityMatch(capability_id, round(min(1.0, score), 3), via)


class EmbeddingCapabilityMatcher:
    """Segunda estrategia de matching (paso 6 del ROADMAP, primer bloque):
    misma interfaz `CapabilityMatcher`, cobertura semántica local en vez de
    solape léxico.

    ⚠ Reutiliza `HashingEmbedder` (`kernel/rag/embedder.py`): random indexing
    con pesos TF-IDF sobre unigramas, bigramas y prefijos. **No es un
    embedding neuronal** — no hay bge-m3, sentence-transformers ni torch
    aquí. Lo que aporta sobre `LexicalCapabilityMatcher` no es sinonimia real
    (`HashingEmbedder` tampoco la tiene: "paro" y "desempleo" siguen sin
    parecerse por sí solos) sino robustez a la REDACCIÓN de la consulta: dos
    frases que comparten poco vocabulario exacto pero describen lo mismo con
    palabras parecidas (prefijos, bigramas) pueden acercarse en el espacio
    vectorial aunque el solape de tokens crudo sea débil.

    El texto indexado por capacidad es `name + description + routing_examples
    + synonyms` — el mismo vocabulario de dominio que usa el matcher léxico,
    para que ambos canales compitan sobre la misma fuente de verdad
    (`capabilities/*.capability.yaml`), no sobre corpus distintos.

    Dos señales, no una
    -------------------
    1. **Coseno** contra el vector de cada capacidad — señal continua,
       ruidosa por construcción (la proyección aleatoria del embedder da
       *algo* de coseno positivo incluso para texto sin relación real; ver
       `kernel/rag/embedder.py` y su equivalente en `vector_store.py`).
    2. **Sinónimo exacto** — si algún `synonyms` declarado de la capacidad
       aparece literalmente en la consulta (normalizada, sin acentos), se
       marca `via="synonym"` con la confianza máxima: es una coincidencia
       exacta de vocabulario de dominio, no una similitud aproximada, y por
       eso es la señal más auditable y menos discutible de las dos.

    Antes de propagar por la taxonomía (ancestros/relacionados, igual que
    `LexicalCapabilityMatcher`) se filtra el ruido de fondo del coseno con
    `NOISE_FLOOR` — sin ese filtro, el ruido se acumularía a través de
    ancestros y podría cruzar el umbral final para una capacidad totalmente
    ajena a la consulta. El valor se mide contra el catálogo real, no se
    adivina (ver `evaluation/bench_routing.py` y el comentario junto a la
    constante).
    """

    #: Piso de coseno ANTES de propagar. Medido sobre las 17 capacidades
    #: reales del repositorio con ~25 consultas de prueba (genuinas y sin
    #: relación): el ruido de fondo entre una consulta cualquiera y una
    #: capacidad SIN relación real llega hasta 0.187 ("qué es un smartphone
    #: plegable" → 0.187 contra `mental_health`); los aciertos genuinos
    #: rondan 0.25-0.45 solo cuando la consulta comparte vocabulario cercano
    #: al literal de `routing_examples` (0.406 para "quiero invertir mi
    #: dinero", que ES un routing_example de `finance`). **El corpus de
    #: capacidades es demasiado pequeño y corto (17 textos de 20-40
    #: palabras) para que el coseno separe señal de ruido con margen amplio**
    #: — a diferencia del RAG sobre la wiki (1162 chunks), donde sí se pudo
    #: usar un umbral más bajo. 0.30 dejó pasar 0 falsos positivos en la
    #: medición y sigue permitiendo los aciertos casi literales. Con este
    #: umbral, la mayoría de las paráfrasis coloquiales genuinas (verificado:
    #: "quiero mejorar mi alimentación diaria", "necesito ser más eficiente
    #: con mi tiempo") NO cruzan el umbral por coseno solo — es una limitación
    #: real, no oculta: ver `HybridCapabilityMatcher`, donde el canal que sí
    #: aporta cobertura demostrable es el de sinónimos exactos, no el coseno.
    NOISE_FLOOR = 0.30

    def __init__(self, catalog: CapabilityCatalog, *,
                 embedder: HashingEmbedder | None = None,
                 noise_floor: float = NOISE_FLOOR):
        self._catalog = catalog
        self._embedder = embedder or HashingEmbedder(dim=256, name="capability_embedding")
        self._noise_floor = noise_floor
        self._caps: list[Capability] = []
        self._vectors: dict[str, list[float]] = {}
        self._cache: dict[tuple, list[CapabilityMatch]] = {}
        self._build_index()

    @staticmethod
    def _seed_text(cap: Capability) -> str:
        return " ".join([cap.name, cap.description, *cap.routing_examples, *cap.synonyms])

    def _build_index(self) -> None:
        self._caps = self._catalog.all()
        seeds = [self._seed_text(c) for c in self._caps]
        if not self._embedder.ajustado:
            self._embedder.fit(seeds)
        for cap, vector in zip(self._caps, self._embedder.embed(seeds)):
            self._vectors[cap.id] = vector

    def _synonym_hit(self, query_norm: str, cap: Capability) -> bool:
        return any(_norm(s) in query_norm for s in cap.synonyms)

    def _raw_scores(self, query: str) -> dict[str, tuple[float, str]]:
        """{capability_id: (score, via)} para lo que supera `NOISE_FLOOR`.

        `via` es "synonym" (coincidencia exacta de vocabulario) o "embedding"
        (solo similitud vectorial) — la fuente más fuerte, antes de propagar.
        """
        [qvec] = self._embedder.embed([query])
        if not any(qvec):
            return {}
        query_norm = _norm(query)
        raw: dict[str, tuple[float, str]] = {}
        for cap in self._caps:
            score = max(0.0, coseno(qvec, self._vectors[cap.id]))
            via = "embedding"
            if self._synonym_hit(query_norm, cap):
                score = 1.0   # coincidencia exacta: confianza máxima antes de propagar
                via = "synonym"
            if score >= self._noise_floor:
                raw[cap.id] = (score, via)
        return raw

    def _propagate(self, raw: dict[str, tuple[float, str]]) -> dict[str, tuple[float, str]]:
        """Misma propagación jerárquica que `LexicalCapabilityMatcher`
        (ancestros con decaimiento, relacionados con peso fijo), pero
        etiquetando la vía con el vocabulario auditable de este matcher:
        "parent" en vez de "ancestor" — más claro para quien lea la traza.
        """
        totals: dict[str, float] = {cid: s for cid, (s, _) in raw.items()}
        via: dict[str, str] = {cid: v for cid, (_, v) in raw.items()}

        def _accumulate(cap_id: str, amount: float, source: str) -> None:
            totals[cap_id] = totals.get(cap_id, 0.0) + amount
            if cap_id not in raw:  # no tiene señal propia: la propagación decide la vía
                via.setdefault(cap_id, source)

        for cap_id, (base, _) in raw.items():
            cap = self._catalog.get(cap_id)
            if cap is None:
                continue
            for depth, ancestor_id in enumerate(self._catalog.ancestors_of(cap_id), start=1):
                _accumulate(ancestor_id, base * (_ANCESTOR_DECAY ** depth), "parent")
            for rel_id in cap.related:
                _accumulate(rel_id, base * _RELATED_WEIGHT, "related")

        return {cid: (min(1.0, s), via.get(cid, "embedding")) for cid, s in totals.items()}

    def match(self, query: str, *, k: int = 5, min_score: float = 0.35) -> list[CapabilityMatch]:
        cache_key = (query, k, min_score)
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = self._raw_scores(query)
        combined = self._propagate(raw)
        result = [CapabilityMatch(cid, round(min(1.0, s), 3), via)
                  for cid, (s, via) in combined.items() if s >= min_score]
        result.sort(key=lambda m: m.score, reverse=True)
        result = result[:k]
        self._cache[cache_key] = result
        return result

    def explain(self, query: str, capability_id: str) -> CapabilityMatch | None:
        raw = self._raw_scores(query)
        combined = self._propagate(raw)
        if capability_id not in combined:
            return None
        score, via = combined[capability_id]
        return CapabilityMatch(capability_id, round(min(1.0, score), 3), via)


@dataclass(frozen=True)
class HybridChannelScores:
    """Desglose por canal de una capacidad — lo que pide `explain()` (paso 6).

    Existe porque "capacidad candidata, score léxico, score vectorial, score
    final, motivo, umbral aplicado" no cabe en un `CapabilityMatch` (que solo
    tiene un score y un `via`) sin perder la trazabilidad de CADA canal por
    separado.
    """
    capability_id: str
    lexical_score: float
    embedding_score: float
    final_score: float
    via: str
    reason: str
    threshold_applied: float


class HybridCapabilityMatcher:
    """Combina `LexicalCapabilityMatcher` + `EmbeddingCapabilityMatcher` con
    una política de selección explícita, no un promedio ciego de dos escalas
    (paso 6 del ROADMAP, primer bloque).

    Por qué esta combinación y no otra — medido, no supuesto
    -----------------------------------------------------------------------
    Antes de escribir esta clase se midió el coseno de `EmbeddingCapabilityMatcher`
    contra las 17 capacidades reales con ~25 consultas (genuinas, coloquiales
    y sin relación). Resultado: el canal de coseno puro es **más débil que el
    léxico en todos los casos probados**, y para varias consultas con
    vocabulario real (p. ej. "quiero mejorar mi alimentación diaria") su
    capacidad top-1 era la INCORRECTA (`project_management` en vez de
    `nutrition`) mientras el léxico acertaba con holgura. Dejar que el coseno
    decidiera por sí solo habría **aumentado** las rutas incorrectas — justo
    lo que este bloque del roadmap prohíbe. Por eso:

      - El canal léxico manda: una capacidad que el léxico ya identifica
        (`lexical_score >= lexical_min_score`) se incluye siempre, con su
        vía y su score léxico intactos — "el léxico debe seguir aportando
        coincidencias exactas y explicabilidad".
      - El canal de **sinónimo exacto** (declarado en `capabilities/*.yaml`,
        detectado por `EmbeddingCapabilityMatcher`) es la única vía por la
        que el canal vectorial puede incluir una capacidad POR SÍ SOLO,
        porque es determinista y curado por un humano, no una similitud
        difusa — bajo riesgo real de falso positivo.
      - El coseno puro (ni léxico, ni sinónimo) solo puede: (a) reforzar
        —sumar un poco— el score de una capacidad que el léxico YA
        encontró, nunca crear una de la nada; o (b) incluir una capacidad
        por sí solo si supera `EMBEDDING_MIN_SCORE` (0.30 — el mismo piso
        medido en `EmbeddingCapabilityMatcher.NOISE_FLOOR`, con margen de
        0.11 sobre el ruido de fondo observado). En la medición, ninguna
        consulta sin relación cruzó ese umbral.

    Vías reportadas (auditables): `synonym` (coincidencia exacta), `hybrid`
    (léxico y vectorial coinciden en la misma capacidad), `lexical` (solo
    léxico), `embedding` (solo coseno, por encima de su propio umbral),
    `parent`/`related` (la capacidad no matcheó directo; llegó por
    propagación de taxonomía en cualquiera de los dos canales).
    """

    #: Umbral del canal léxico. Es el mismo valor que usa `LexicalCapabilityMatcher`
    #: en solitario (y el que ya usan los agentes reales vía `ROUTING_MIN_SCORE`
    #: en `orchestration/engine.py`) — no se inventa uno nuevo para el híbrido.
    LEXICAL_MIN_SCORE = 0.35

    #: Umbral del canal vectorial puro (no sinónimo). Igual a
    #: `EmbeddingCapabilityMatcher.NOISE_FLOOR`: 0.30, medido con margen de
    #: 0.11 sobre el ruido de fondo observado (ver esa constante).
    EMBEDDING_MIN_SCORE = EmbeddingCapabilityMatcher.NOISE_FLOOR

    #: Cuánto puede sumar el coseno al score léxico de una capacidad que el
    #: léxico ya encontró. Deliberadamente pequeño: el coseno demostró ser
    #: menos fiable que el léxico en la medición, así que solo puede
    #: reforzar un match existente (nunca crear uno), y solo un poco — no
    #: alcanza por sí solo para cruzar el umbral desde 0.
    REINFORCEMENT_WEIGHT = 0.10

    def __init__(self, catalog: CapabilityCatalog, *,
                 lexical: CapabilityMatcher | None = None,
                 embedding: EmbeddingCapabilityMatcher | None = None,
                 lexical_min_score: float = LEXICAL_MIN_SCORE,
                 embedding_min_score: float = EMBEDDING_MIN_SCORE,
                 reinforcement_weight: float = REINFORCEMENT_WEIGHT):
        self._lexical = lexical or LexicalCapabilityMatcher(catalog)
        self._embedding = embedding or EmbeddingCapabilityMatcher(catalog)
        self._lexical_min_score = lexical_min_score
        self._embedding_min_score = embedding_min_score
        self._reinforcement_weight = reinforcement_weight
        self._cache: dict[tuple, list[CapabilityMatch]] = {}

    # -- combinación por capacidad --------------------------------------------
    def _channels(self, query: str) -> dict[str, tuple[float, str, float, str]]:
        """{capability_id: (lex_score, lex_via, emb_score, emb_via)}."""
        lex = {m.capability_id: m for m in self._lexical.match(query, k=1000, min_score=0.0)}
        emb = {m.capability_id: m for m in self._embedding.match(query, k=1000, min_score=0.0)}
        out: dict[str, tuple[float, str, float, str]] = {}
        for cid in set(lex) | set(emb):
            lm, em = lex.get(cid), emb.get(cid)
            out[cid] = (lm.score if lm else 0.0, lm.via if lm else "",
                        em.score if em else 0.0, em.via if em else "")
        return out

    def _combine(self, lex_score: float, lex_via: str,
                emb_score: float, emb_via: str) -> tuple[float, str, bool]:
        """Devuelve (score_final, via, incluida)."""
        lex_passes = lex_score >= self._lexical_min_score
        is_synonym = emb_via == "synonym"
        emb_passes_alone = emb_via in ("embedding", "parent", "related") and \
            emb_score >= self._embedding_min_score

        if not (lex_passes or is_synonym or emb_passes_alone):
            return 0.0, "", False

        if is_synonym:
            return max(lex_score, emb_score), "synonym", True

        if lex_passes and emb_passes_alone:
            score = min(1.0, lex_score + self._reinforcement_weight * emb_score)
            return score, "hybrid", True

        if lex_passes:
            # el coseno no alcanzó su propio umbral, pero si tiene ALGO de
            # señal para la MISMA capacidad, refuerza (nunca decide solo).
            score = min(1.0, lex_score + self._reinforcement_weight * emb_score)
            via = {"direct": "lexical", "ancestor": "parent", "related": "related"}.get(
                lex_via, "lexical")
            return score, via, True

        # solo pasa el canal vectorial (coseno puro, por encima de su propio umbral)
        via = {"embedding": "embedding", "parent": "parent", "related": "related"}.get(
            emb_via, "embedding")
        return emb_score, via, True

    def match(self, query: str, *, k: int = 5, min_score: float = 0.35) -> list[CapabilityMatch]:
        cache_key = (query, k, min_score)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = []
        for cid, (lex_s, lex_v, emb_s, emb_v) in self._channels(query).items():
            score, via, incluida = self._combine(lex_s, lex_v, emb_s, emb_v)
            if incluida and score >= min_score:
                result.append(CapabilityMatch(cid, round(min(1.0, score), 3), via))
        result.sort(key=lambda m: m.score, reverse=True)
        result = result[:k]
        self._cache[cache_key] = result
        return result

    def explain(self, query: str, capability_id: str) -> CapabilityMatch | None:
        channels = self._channels(query)
        if capability_id not in channels:
            return None
        lex_s, lex_v, emb_s, emb_v = channels[capability_id]
        score, via, incluida = self._combine(lex_s, lex_v, emb_s, emb_v)
        if not incluida:
            return None
        return CapabilityMatch(capability_id, round(min(1.0, score), 3), via)

    def explain_detailed(self, query: str, capability_id: str,
                         *, min_score: float = 0.35) -> HybridChannelScores | None:
        """Desglose completo por canal — lo que pide `CapabilityEngine.explain()`:
        score léxico, score vectorial, score final, motivo y umbral aplicado.

        A diferencia de `explain()` (que devuelve `None` si la capacidad no
        se incluiría), esto SIEMPRE devuelve el desglose si la capacidad
        existe en el catálogo, aunque no haya pasado ningún canal — es lo
        que permite auditar POR QUÉ algo no se enrutó, no solo qué sí.
        """
        channels = self._channels(query)
        lex_s, lex_v, emb_s, emb_v = channels.get(capability_id, (0.0, "", 0.0, ""))
        score, via, incluida = self._combine(lex_s, lex_v, emb_s, emb_v)

        if not incluida:
            razon = (f"ningún canal alcanzó su umbral (léxico {lex_s:.3f} < "
                    f"{self._lexical_min_score}, vectorial {emb_s:.3f} < "
                    f"{self._embedding_min_score}, sin sinónimo exacto)")
            return HybridChannelScores(capability_id, lex_s, emb_s, 0.0, "sin_match",
                                       razon, min_score)

        razones = {
            "synonym": "coincidencia exacta de sinónimo declarado en la capacidad",
            "hybrid": (f"léxico ({lex_s:.3f}) y vectorial ({emb_s:.3f}) superan sus "
                      f"propios umbrales de forma independiente"),
            "lexical": f"léxico ({lex_s:.3f}) supera su umbral ({self._lexical_min_score})",
            "embedding": f"vectorial ({emb_s:.3f}) supera su umbral ({self._embedding_min_score})",
            "parent": "propagado desde un descendiente de la taxonomía",
            "related": "propagado desde una capacidad relacionada",
        }
        return HybridChannelScores(capability_id, lex_s, emb_s, round(min(1.0, score), 3),
                                   via, razones.get(via, via), min_score)
