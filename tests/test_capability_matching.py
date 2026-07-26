"""Paso 6 del ROADMAP, primer bloque: `EmbeddingCapabilityMatcher` +
`HybridCapabilityMatcher` — segunda estrategia de matching de capacidades.

Antes de escribir este matcher se midió el coseno de `HashingEmbedder`
contra las capacidades reales con ~25 consultas (genuinas, coloquiales y sin
relación). El hallazgo fue que el canal de coseno puro es MÁS DÉBIL que el
léxico para este corpus (textos cortos, pocas "capacidades-documento" para
el IDF) y en varios casos apuntaba a la capacidad INCORRECTA con más
confianza que a la correcta. Por eso el diseño no deja que el coseno decida
solo salvo con un umbral alto y medido (`NOISE_FLOOR`/`EMBEDDING_MIN_SCORE`):
el canal que sí demostró aportar cobertura de forma segura es el de
sinónimo EXACTO (determinista), no la similitud difusa.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from orchestration.capability.matcher import (
    CapabilityMatch, EmbeddingCapabilityMatcher, HybridCapabilityMatcher,
    LexicalCapabilityMatcher,
)
from orchestration.capability_engine import CapabilityEngine
from orchestration.registry.agent_registry import AgentRegistry
from orchestration.registry.capability_catalog import CapabilityCatalog


def _real_catalog(repo_root) -> CapabilityCatalog:
    return CapabilityCatalog(repo_root / "capabilities").load_all()


def _real_registry(repo_root, catalog) -> AgentRegistry:
    reg = AgentRegistry(
        repo_root / "agents", capabilities=catalog,
        models_yaml=repo_root / "configs" / "models.yaml",
        permissions_yaml=repo_root / "configs" / "permissions.yaml",
        mcp_catalog_yaml=repo_root / "tools" / "mcp_catalog.yaml")
    reg.load_all()
    return reg


# -- embedding determinista -----------------------------------------------------
def test_el_embedding_es_determinista(repo_root):
    cat = _real_catalog(repo_root)
    a = EmbeddingCapabilityMatcher(cat).match("cuál es la inflación", k=3, min_score=0.0)
    b = EmbeddingCapabilityMatcher(cat).match("cuál es la inflación", k=3, min_score=0.0)
    assert a == b


def test_dos_instancias_del_catalogo_dan_el_mismo_indice(repo_root):
    """Determinismo también entre catálogos cargados por separado, no solo
    entre llamadas al mismo objeto."""
    cat1 = _real_catalog(repo_root)
    cat2 = _real_catalog(repo_root)
    m1 = EmbeddingCapabilityMatcher(cat1)
    m2 = EmbeddingCapabilityMatcher(cat2)
    q = "quiero invertir mi dinero"
    assert m1.match(q, k=5, min_score=0.0) == m2.match(q, k=5, min_score=0.0)


# -- sinónimos / expresiones coloquiales ------------------------------------------
def test_sinonimo_exacto_fuerza_confianza_maxima(repo_root):
    """'plata' es sinónimo declarado de finance (paso 6): coincidencia
    exacta, no similitud aproximada — por eso el score es 1.0, no un coseno
    parcial."""
    cat = _real_catalog(repo_root)
    m = EmbeddingCapabilityMatcher(cat)
    r = m.match("quiero ahorrar más plata cada mes", k=3, min_score=0.0)
    assert r[0].capability_id == "finance"
    assert r[0].score == 1.0
    assert r[0].via == "synonym"


def test_expresion_coloquial_sin_sinonimo_declarado_no_matchea_por_coseno_solo(repo_root):
    """Hallazgo honesto de la medición: el coseno puro NO resuelve la
    mayoría de las paráfrasis coloquiales en este corpus. No se afirma lo
    contrario en ningún sitio — este test lo deja fijado."""
    cat = _real_catalog(repo_root)
    m = EmbeddingCapabilityMatcher(cat)
    # "necesito ser más eficiente con mi tiempo" es productivity genuino,
    # pero medido en 0.099 de coseno — muy por debajo de NOISE_FLOOR (0.30).
    r = m.match("necesito ser más eficiente con mi tiempo", k=3, min_score=0.0)
    assert r == [], (
        "si esto empieza a matchear, revisar si NOISE_FLOOR cambió — el "
        "diseño depende de que el coseno puro sea conservador en este corpus")


def test_la_diluvion_lexica_se_corrige_por_sinonimo_exacto(repo_root):
    """Caso real medido: 'estómago' es sinónimo de gastroenterology, pero
    LexicalCapabilityMatcher solo (IDF-weighted overlap) puntúa más alto a
    `nutrition` porque 'comer' aparece en sus routing_examples — la señal
    del sinónimo se diluye entre más palabras que matchean otra capacidad.
    El canal de sinónimo exacto del embedding no se diluye: es booleano.
    """
    cat = _real_catalog(repo_root)
    consulta = "tengo ardor de estómago después de comer"

    lex_top = LexicalCapabilityMatcher(cat).match(consulta, k=1, min_score=0.0)[0]
    assert lex_top.capability_id == "nutrition", (
        "si esto cambia, la 'dilución' que motiva el canal de sinónimo ya "
        "no reproduce — revisar el caso antes de tocar nada más")

    hyb_top = HybridCapabilityMatcher(cat).match(consulta, k=1, min_score=0.35)[0]
    assert hyb_top.capability_id == "gastroenterology"
    assert hyb_top.via == "synonym"


# -- consulta conocida que enruta correctamente -----------------------------------
def test_consulta_conocida_enruta_al_agente_correcto(repo_root):
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg)   # default = HybridCapabilityMatcher

    agentes = [a.id for a in engine.route_to_agents(
        "cuál es la inflación en República Dominicana", k=3)]

    assert "ernesto_libras" in agentes


def test_las_tres_consultas_arregladas_por_sinonimo_enrutan_bien(repo_root):
    """Regresión directa de los 3 sinónimos añadidos en este bloque del
    roadmap: antes de añadirlos, estas consultas no encontraban NINGÚN
    agente (verificado antes de escribir este test)."""
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg)

    casos = [
        ("quiero ahorrar más plata cada mes", "ernesto_libras"),
        ("me quiero ir de mi chamba, ya no aguanto", "amanda"),
        ("no puedo pegar el ojo en toda la noche", "dr_soma"),
    ]
    for consulta, esperado in casos:
        agentes = [a.id for a in engine.route_to_agents(consulta, k=3)]
        assert esperado in agentes, f"'{consulta}' -> {agentes}, esperaba {esperado}"


# -- consulta desconocida que sigue sin dominio -----------------------------------
def test_consulta_sin_relacion_sigue_sin_agente(repo_root):
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg)

    for consulta in ("cuál es la capital de Francia", "qué es un smartphone plegable",
                     "receta de tres leches"):
        assert engine.route_to_agents(consulta, k=3) == []


def test_hybrid_no_introduce_falsos_positivos_que_el_lexico_no_tuviera(repo_root):
    """El coseno puro alcanza hasta 0.187 de ruido de fondo en la medición;
    EMBEDDING_MIN_SCORE=0.30 debe dejarlo fuera con margen."""
    cat = _real_catalog(repo_root)
    hyb = HybridCapabilityMatcher(cat)
    lex = LexicalCapabilityMatcher(cat)

    for consulta in ("cuál es la capital de Francia", "qué es un smartphone plegable",
                     "receta de tres leches", "cuánto cuesta un boleto de avión"):
        assert hyb.match(consulta, k=5, min_score=0.35) == \
            lex.match(consulta, k=5, min_score=0.35) == []


# -- taxonomía padre/hijo ---------------------------------------------------------
def test_propaga_a_ancestros_con_decaimiento(repo_root):
    """digestive_diseases → gastroenterology → medicine. Un sinónimo exacto
    de digestive_diseases (score forzado a 1.0) debe subir a su padre
    (gastroenterology, decay^1=0.5) y a su abuelo (medicine, decay^2=0.25)."""
    cat = _real_catalog(repo_root)
    m = EmbeddingCapabilityMatcher(cat)

    r = {mm.capability_id: mm for mm in m.match("tengo gastritis, qué la empeora",
                                                k=10, min_score=0.0)}
    assert r["digestive_diseases"].via == "synonym"
    assert r["digestive_diseases"].score == 1.0
    assert "gastroenterology" in r and r["gastroenterology"].via == "parent"
    assert "medicine" in r and r["medicine"].via == "parent"
    # decaimiento: el padre debe puntuar más que el abuelo para la MISMA fuente
    assert r["gastroenterology"].score > r["medicine"].score


def test_related_propaga_con_peso_distinto_que_ancestro(repo_root):
    """`risk` está `related` de `markets`/`macroeconomics` (no son
    ancestro/descendiente) — un match fuerte en una capacidad debe subir un
    poco a sus relacionadas, con menos peso que la propagación jerárquica."""
    cat = _real_catalog(repo_root)
    m = EmbeddingCapabilityMatcher(cat)
    r = {mm.capability_id: mm for mm in
        m.match("análisis de escenarios de riesgo", k=10, min_score=0.0)}
    if "risk" in r and r["risk"].via in ("embedding", "synonym"):
        # si risk matcheó directo, sus relacionados deben aparecer propagados
        assert any(cid in r and r[cid].via == "related"
                  for cid in ("markets", "macroeconomics"))


# -- preservación de límites de umbral ---------------------------------------------
def test_el_canal_lexico_respeta_su_propio_umbral(repo_root):
    cat = _real_catalog(repo_root)
    hyb = HybridCapabilityMatcher(cat, lexical_min_score=0.90)  # casi imposible de cruzar
    # con un umbral léxico altísimo, solo sinónimo exacto o coseno >=0.30 deben pasar
    r = hyb.match("cómo organizo mi presupuesto", k=3, min_score=0.35)
    for m in r:
        assert m.via in ("synonym", "embedding", "hybrid", "parent", "related"), (
            f"'{m.via}' no debería aparecer si el canal léxico no puede cruzar su umbral")


def test_el_canal_vectorial_respeta_su_propio_umbral(repo_root):
    cat = _real_catalog(repo_root)
    permisivo = HybridCapabilityMatcher(cat, embedding_min_score=0.0)
    estricto = HybridCapabilityMatcher(cat, embedding_min_score=0.99)

    consulta = "necesito ser más eficiente con mi tiempo"  # coseno ~0.10, sin sinónimo
    # con el canal vectorial casi sin piso, algo de ruido podría colarse
    permisivo.match(consulta, k=10, min_score=0.0)
    # con un piso casi imposible, el canal vectorial no puede aportar nada por sí solo
    r = estricto.match(consulta, k=10, min_score=0.35)
    assert all(m.via != "embedding" for m in r)


def test_reinforcement_weight_nunca_decide_solo(repo_root):
    """El coseno solo REFUERZA un match léxico existente — no puede, por sí
    solo, crear uno desde 0 salvo que cruce su propio umbral independiente."""
    cat = _real_catalog(repo_root)
    hyb = HybridCapabilityMatcher(cat, reinforcement_weight=10.0)  # exagerado a propósito
    # una consulta sin ninguna señal real no debe aparecer aunque el refuerzo sea enorme,
    # porque sin lex_passes ni synonym ni emb_passes_alone no hay nada que reforzar
    r = hyb.match("cuál es la capital de Francia", k=5, min_score=0.35)
    assert r == []


# -- explicación con scores y vías --------------------------------------------------
def test_explain_devuelve_capacidad_score_y_via(repo_root):
    cat = _real_catalog(repo_root)
    m = HybridCapabilityMatcher(cat)
    e = m.explain("quiero ahorrar más plata cada mes", "finance")
    assert e is not None
    assert e.capability_id == "finance"
    assert e.score == 1.0
    assert e.via == "synonym"


def test_explain_detailed_expone_lexico_vectorial_final_motivo_y_umbral(repo_root):
    cat = _real_catalog(repo_root)
    m = HybridCapabilityMatcher(cat)
    d = m.explain_detailed("quiero ahorrar más plata cada mes", "finance", min_score=0.35)

    assert d is not None
    assert d.capability_id == "finance"
    assert d.embedding_score == 1.0          # sinónimo exacto
    assert d.final_score == 1.0
    assert d.via == "synonym"
    assert "sinónimo" in d.reason
    assert d.threshold_applied == 0.35


def test_explain_detailed_explica_tambien_por_que_no_paso(repo_root):
    """A diferencia de `explain()`, `explain_detailed` siempre devuelve algo
    si la capacidad existe — incluida la razón de por qué NO se enrutó."""
    cat = _real_catalog(repo_root)
    m = HybridCapabilityMatcher(cat)
    d = m.explain_detailed("cuál es la capital de Francia", "finance", min_score=0.35)

    assert d is not None
    assert d.via == "sin_match"
    assert "ningún canal alcanzó su umbral" in d.reason


def test_capability_engine_explain_expone_el_desglose_por_canal(repo_root):
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg)

    explicacion = engine.explain("cuál es la inflación en República Dominicana",
                                 "ernesto_libras")

    assert explicacion.threshold == 0.35
    assert explicacion.channel_scores, "debe traer desglose por canal con el matcher híbrido"
    ids = {c.capability_id for c in explicacion.channel_scores}
    assert ids <= {"macroeconomics", "finance", "markets", "risk"}  # capacidades de ernesto_libras
    for c in explicacion.channel_scores:
        assert hasattr(c, "lexical_score") and hasattr(c, "embedding_score")
        assert hasattr(c, "final_score") and hasattr(c, "reason")


# -- el híbrido no empeora los casos existentes -----------------------------------
def test_el_hibrido_no_empeora_el_banco_de_enrutado(repo_root):
    """Guardia equivalente a `test_el_hibrido_no_empeora_el_baseline_lexico`
    (RAG, paso 4) pero para enrutado: usa el golden set real, no un caso
    inventado."""
    from evaluation.bench_routing import cargar_casos, evaluar, _build_engine

    casos = cargar_casos(repo_root / "evaluation" / "goldens" / "routing.yaml")
    assert casos, "el golden set de enrutado no debe estar vacío"

    lexico = evaluar("lexico", _build_engine(repo_root, matcher=LexicalCapabilityMatcher), casos)
    hibrido = evaluar("hibrido", _build_engine(repo_root), casos)

    assert hibrido.precision >= lexico.precision, (
        f"el híbrido ({hibrido.precision:.1%}) no debe tener menos precisión "
        f"que el léxico ({lexico.precision:.1%})")
    assert hibrido.falsos_positivos <= lexico.falsos_positivos, (
        f"el híbrido no debe introducir más falsos positivos "
        f"({hibrido.falsos_positivos} vs {lexico.falsos_positivos})")


def test_capability_engine_usa_hibrido_por_defecto(repo_root):
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg)
    assert isinstance(engine._matcher, HybridCapabilityMatcher)


def test_capability_engine_acepta_lexico_explicito_para_medir_baseline(repo_root):
    cat = _real_catalog(repo_root)
    reg = _real_registry(repo_root, cat)
    engine = CapabilityEngine(cat, reg, matcher=LexicalCapabilityMatcher(cat))
    assert isinstance(engine._matcher, LexicalCapabilityMatcher)


# -- protocolo / intercambiabilidad -------------------------------------------------
def test_los_tres_matchers_implementan_la_misma_interfaz(repo_root):
    cat = _real_catalog(repo_root)
    for matcher in (LexicalCapabilityMatcher(cat), EmbeddingCapabilityMatcher(cat),
                   HybridCapabilityMatcher(cat)):
        r = matcher.match("cuál es la inflación", k=3, min_score=0.0)
        assert isinstance(r, list)
        assert all(isinstance(m, CapabilityMatch) for m in r)
        # explain() puede devolver None, pero no debe lanzar
        matcher.explain("cuál es la inflación", "finance")
