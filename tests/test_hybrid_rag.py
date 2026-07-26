"""Paso 4 del ROADMAP: RAG realmente híbrido (denso + léxico).

El defecto original: `RAGPipeline(self.store, self.store)` — el mismo
`FileWikiStore` pasado como retriever denso Y léxico. El pipeline decía
"híbrido" haciendo dos veces lo mismo.
"""
from __future__ import annotations

from kernel.rag.embedder import HashingEmbedder, coseno
from kernel.rag.file_store import FileWikiStore
from kernel.rag.pipeline import RAGPipeline, RAGRequest
from kernel.rag.vector_store import InMemoryVectorStore
from orchestration.engine import MagnusEngine


def _store(root) -> FileWikiStore:
    s = FileWikiStore(root / "LLM-Wiki" / "wiki")
    s.ingest()
    return s


# -- embedder ------------------------------------------------------------------
def test_produce_vectores_densos_normalizados():
    emb = HashingEmbedder(dim=64).fit(["la inflación subió", "dormir bien importa"])
    [v] = emb.embed(["la inflación subió"])

    assert len(v) == 64
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9, "debe estar normalizado a norma 1"


def test_es_determinista():
    a = HashingEmbedder(dim=64).fit(["texto de corpus"]).embed(["consulta"])
    b = HashingEmbedder(dim=64).fit(["texto de corpus"]).embed(["consulta"])
    assert a == b


def test_un_texto_se_parece_mas_a_si_mismo_que_a_otro():
    corpus = ["la inflación y las tasas del banco central",
              "higiene del sueño y horarios para dormir"]
    emb = HashingEmbedder(dim=128).fit(corpus)
    v_inf, v_sueno = emb.embed(corpus)
    [q] = emb.embed(["qué pasa con la inflación"])

    assert coseno(q, v_inf) > coseno(q, v_sueno)


def test_los_prefijos_acercan_variantes_morfologicas():
    """En español esto importa: 'inflación' / 'inflacionario'."""
    corpus = ["proceso inflacionario sostenido en el tiempo",
              "rutina de ejercicio y descanso semanal"]
    emb = HashingEmbedder(dim=128).fit(corpus)
    v_inflacion, v_ejercicio = emb.embed(corpus)
    [q] = emb.embed(["inflación"])

    assert coseno(q, v_inflacion) > coseno(q, v_ejercicio)


def test_el_idf_baja_el_peso_de_lo_que_esta_en_todas_partes():
    comun = ["banco importante uno", "banco importante dos", "banco importante tres"]
    emb = HashingEmbedder(dim=128).fit(comun + ["glinfatico sistema cerebral"])

    assert emb._idf["banco"] < emb._idf["glinfatico"]


def test_una_consulta_vacia_no_recupera_nada(mini_root):
    vs = InMemoryVectorStore.from_wiki_store(_store(mini_root))
    assert vs.retrieve("de la y el", [], 5) == []


# -- vector store ---------------------------------------------------------------
def test_indexa_los_mismos_chunks_que_el_store_lexico(mini_root):
    store = _store(mini_root)
    vs = InMemoryVectorStore.from_wiki_store(store)

    assert len(vs) == len(store.documents())

    # mismos chunk_id: es lo que permite deduplicar al fusionar
    ids_lexico = {c.chunk_id for c in store.retrieve("inflación", ["01-Finanzas"], 10)}
    ids_denso = {c.chunk_id for c in vs.retrieve("inflación", ["01-Finanzas"], 10)}
    assert ids_denso & ids_lexico


def test_respeta_el_namespace_del_agente(mini_root):
    vs = InMemoryVectorStore.from_wiki_store(_store(mini_root))

    dentro = vs.retrieve("inflación República Dominicana", ["01-Finanzas"], 5)
    assert dentro and all(c.namespace == "01-Finanzas" for c in dentro)

    fuera = vs.retrieve("inflación República Dominicana", ["02-Sueno"], 5)
    assert all(c.namespace == "02-Sueno" for c in fuera), "la parcela es un límite duro"
    assert not any("Finanzas" in c.provenance.source for c in fuera)


def test_el_denso_devuelve_ruido_debil_donde_el_lexico_devuelve_vacio(mini_root):
    """Diferencia real entre los dos retrievers, y por qué el umbral importa.

    El léxico no devuelve nada si no hay solape de tokens; el denso siempre
    tiene algo de coseno positivo por la proyección aleatoria. Ese ruido lo
    filtra `min_score`, no el retriever — por eso el umbral del agente sigue
    siendo la pieza que decide qué cuenta como evidencia.
    """
    store = _store(mini_root)
    vs = InMemoryVectorStore.from_wiki_store(store)
    consulta = "inflación República Dominicana"

    assert store.retrieve(consulta, ["02-Sueno"], 5) == []
    ruido = vs.retrieve(consulta, ["02-Sueno"], 5)
    assert ruido, "el denso sí puntúa (débilmente) fuera de tema"
    assert max(c.score for c in ruido) < 0.30, "pero por debajo de cualquier umbral útil"


def test_arrastra_hash_y_snapshot_igual_que_el_lexico(mini_root):
    store = _store(mini_root)
    vs = InMemoryVectorStore.from_wiki_store(store)

    c = vs.retrieve("inflación República Dominicana", ["01-Finanzas"], 1)[0]
    assert c.provenance.hash
    assert c.provenance.knowledge_version == f"wiki:{store.snapshot_id}"


# -- pipeline: fusión ------------------------------------------------------------
def test_el_pipeline_recibe_dos_implementaciones_distintas(mini_root):
    """Ni el mismo objeto, ni la misma clase, ni el mismo algoritmo."""
    engine = MagnusEngine(mini_root)
    vectorial, lexico = engine.rag._dense, engine.rag._lexical

    assert vectorial is not lexico
    assert type(vectorial) is not type(lexico)
    assert isinstance(vectorial, InMemoryVectorStore)
    assert isinstance(lexico, FileWikiStore)
    # `retrieve` viene de clases distintas: no es el mismo código con otro nombre
    assert (type(vectorial).retrieve is not type(lexico).retrieve)


def test_los_dos_retrievers_puntuan_distinto_el_mismo_chunk(mini_root):
    """Prueba de que el híbrido aporta señal, no una copia de la misma.

    Si ambos devolvieran los mismos scores en el mismo orden, la fusión no
    tendría nada que fusionar y el 'híbrido' seguiría siendo léxico duplicado.
    """
    store = _store(mini_root)
    vectorial = InMemoryVectorStore.from_wiki_store(store)
    consulta = "inflación República Dominicana"

    por_lexico = {c.chunk_id: c.score for c in store.retrieve(consulta, ["01-Finanzas"], 10)}
    por_vector = {c.chunk_id: c.score for c in vectorial.retrieve(consulta, ["01-Finanzas"], 10)}

    comunes = set(por_lexico) & set(por_vector)
    assert comunes, "deben coincidir en al menos un chunk para poder compararlos"
    assert any(por_lexico[cid] != por_vector[cid] for cid in comunes), (
        "los dos retrievers devuelven scores idénticos: no son algoritmos distintos")


def test_la_procedencia_sobrevive_a_la_fusion_del_pipeline(mini_root):
    """hash + snapshot + fuente deben llegar intactos al contexto final.

    Es lo que sostiene el criterio de hecho del paso 3: una cita publicada se
    tiene que poder reproducir. Si la fusión de rankings perdiera la
    `Provenance` por el camino, la evaluación de citas seguiría pasando y la
    trazabilidad se habría roto en silencio.
    """
    store = _store(mini_root)
    engine = MagnusEngine(mini_root)

    ctx = engine.rag.build_context(RAGRequest(
        query="inflación República Dominicana", namespaces=["01-Finanzas"],
        top_k=5, min_score=0.0, require_citations=True))

    assert ctx.chunks
    esperado = f"wiki:{store.snapshot_id}"
    por_id = {d["chunk_id"]: d for d in store.documents()}
    for c in ctx.chunks:
        assert c.provenance.knowledge_version == esperado
        assert c.provenance.hash == por_id[c.chunk_id]["hash"], (
            "el hash debe ser el del pasaje exacto que se entregó")
        assert por_id[c.chunk_id]["source"] in c.provenance.source
    assert [p.hash for p in ctx.citations] == [c.provenance.hash for c in ctx.chunks]


def test_hybrid_false_deja_solo_el_lexico(mini_root):
    engine = MagnusEngine(mini_root, hybrid=False)
    assert engine.vectors is None
    assert engine.rag._dense is engine.rag._lexical


def test_pide_mas_candidatos_que_top_k_a_cada_retriever(mini_root):
    """Sin sobremuestreo la fusión empeoraba el baseline (medido en el banco)."""
    pedidos = []

    class _Espia:
        def __init__(self, real): self._real = real
        def retrieve(self, query, namespaces, k):
            pedidos.append(k)
            return self._real.retrieve(query, namespaces, k)

    store = _store(mini_root)
    pipeline = RAGPipeline(_Espia(InMemoryVectorStore.from_wiki_store(store)),
                           _Espia(store))
    pipeline.build_context(RAGRequest(query="inflación", namespaces=["01-Finanzas"],
                                      top_k=4, min_score=0.0))

    assert pedidos == [32, 32], "4 huecos × sobremuestreo 8 a cada retriever"


def test_la_fusion_ordena_por_rrf_pero_filtra_por_score_propio(mini_root):
    """Son dos preguntas distintas: qué es más relevante vs qué es evidencia."""
    from kernel.rag.pipeline import Provenance, ScoredChunk

    def _chunk(cid, score):
        return ScoredChunk(cid, f"texto {cid}", score, "ns", Provenance(source=f"{cid}.md"))

    class _Fijo:
        def __init__(self, chunks): self._c = chunks
        def retrieve(self, q, ns, k): return self._c[:k]

    # el denso pone primero uno con score bajo; el léxico, uno con score alto
    denso = _Fijo([_chunk("bajo", 0.10), _chunk("alto", 0.90)])
    lexico = _Fijo([_chunk("alto", 0.90), _chunk("bajo", 0.10)])

    ctx = RAGPipeline(denso, lexico).build_context(RAGRequest(
        query="q", namespaces=["ns"], top_k=5, min_score=0.50))

    assert [c.chunk_id for c in ctx.chunks] == ["alto"], "el umbral usa el score, no el RRF"


def test_lo_que_ambos_retrievers_encuentran_sube_en_el_ranking(mini_root):
    from kernel.rag.pipeline import Provenance, ScoredChunk

    def _chunk(cid, score=0.9):
        return ScoredChunk(cid, f"texto {cid}", score, "ns", Provenance(source=f"{cid}.md"))

    class _Fijo:
        def __init__(self, chunks): self._c = chunks
        def retrieve(self, q, ns, k): return self._c[:k]

    # "comun" está 2º en ambas listas; "solo_denso" y "solo_lexico" son 1º en una
    denso = _Fijo([_chunk("solo_denso"), _chunk("comun")])
    lexico = _Fijo([_chunk("solo_lexico"), _chunk("comun")])

    ctx = RAGPipeline(denso, lexico).build_context(RAGRequest(
        query="q", namespaces=["ns"], top_k=3, min_score=0.0))

    assert ctx.chunks[0].chunk_id == "comun", "el acuerdo entre retrievers debe pesar"


# -- banco de recuperación sobre la wiki real ------------------------------------
def test_el_hibrido_no_empeora_el_baseline_lexico(repo_root):
    """Guardia del criterio de hecho del paso 4, sobre la wiki real.

    Se afirma "no empeora" y no un número concreto: el recall depende del
    contenido de la wiki, que el usuario edita. Un umbral fijo se rompería
    sola al añadir notas. Para el número exacto:
    `python -m evaluation.bench_retrieval`.
    """
    from evaluation.bench_retrieval import cargar_casos

    casos = cargar_casos(repo_root / "evaluation" / "goldens" / "retrieval.yaml")
    store = FileWikiStore(repo_root / "LLM-Wiki" / "wiki")
    store.ingest()
    vectores = InMemoryVectorStore.from_wiki_store(store)

    def _recall(pipeline) -> float:
        aciertos = sum(
            caso.acierta([c.provenance.source for c in pipeline.build_context(RAGRequest(
                query=caso.consulta, namespaces=caso.namespaces, top_k=8,
                min_score=0.0, require_citations=False)).chunks])
            for caso in casos)
        return aciertos / len(casos)

    baseline = _recall(RAGPipeline(store, store))
    hibrido = _recall(RAGPipeline(vectores, store))

    # Sin esta guarda la comparación sería vacua: un retriever roto da
    # 0% >= 0% y el test pasaría celebrando una recuperación inservible.
    assert baseline >= 0.5, (
        f"el baseline léxico cayó a {baseline:.1%}: la recuperación está rota, "
        f"y comparar contra ella no significa nada")
    assert hibrido >= baseline, (
        f"el híbrido ({hibrido:.1%}) no debe empeorar el baseline léxico ({baseline:.1%})")


def test_sin_sobremuestreo_la_fusion_es_peor_o_igual(repo_root):
    """Fija la razón por la que `OVERSAMPLE` existe.

    Fusionar dos listas de solo `top_k` elementos hacía que el retriever más
    débil desplazara aciertos del más fuerte, y el 'híbrido' quedaba por debajo
    del baseline léxico. Si alguien baja `OVERSAMPLE` a 1 creyendo que ahorra
    trabajo, este test lo señala.
    """
    from evaluation.bench_retrieval import cargar_casos

    casos = cargar_casos(repo_root / "evaluation" / "goldens" / "retrieval.yaml")
    store = FileWikiStore(repo_root / "LLM-Wiki" / "wiki")
    store.ingest()
    vectores = InMemoryVectorStore.from_wiki_store(store)

    def _recall(pipeline) -> float:
        return sum(
            caso.acierta([c.provenance.source for c in pipeline.build_context(RAGRequest(
                query=caso.consulta, namespaces=caso.namespaces, top_k=8,
                min_score=0.0, require_citations=False)).chunks])
            for caso in casos) / len(casos)

    sin_sobremuestreo = _recall(RAGPipeline(vectores, store, oversample=1))
    con_sobremuestreo = _recall(RAGPipeline(vectores, store))

    assert con_sobremuestreo >= sin_sobremuestreo, (
        f"con sobremuestreo {con_sobremuestreo:.1%} vs sin él {sin_sobremuestreo:.1%}")
