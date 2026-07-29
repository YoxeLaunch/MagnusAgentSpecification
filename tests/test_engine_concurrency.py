"""Paso 2.2 del ROADMAP: llamadas a proveedor en paralelo cuando el enrutado
devuelve más de un agente. Antes, `ask()` invocaba proveedores en serie dentro
del `for a in agents:` — con N agentes, la latencia de la consulta era la
SUMA de N llamadas de red en vez del máximo.
"""
from __future__ import annotations

from orchestration.engine import MagnusEngine
from providers.registry import ProviderRegistry

from magnus_fixtures.fake_provider import FakeProvider, citing_provider


def _providers(mini_root, **adaptadores) -> ProviderRegistry:
    return ProviderRegistry.from_yaml(
        str(mini_root / "configs" / "models.yaml"), adaptadores,
        retries=0, backoff_s=0.0, sleep=lambda _s: None)


def _forzar_ambos_agentes(engine: MagnusEngine) -> None:
    """`fina` y `dormi` no comparten ninguna capacidad real, así que ningún
    texto de consulta los enruta a ambos de forma confiable. Se fuerza el
    enrutado (método privado, aceptable en un test que solo quiere ejercitar
    la Fase 2 de `ask()` con >1 agente) en vez de depender de una consulta
    ambigua que podría dejar de matchear a los dos si cambia el catálogo."""
    fina = engine.registry.get("fina")
    dormi = engine.registry.get("dormi")
    engine._route = lambda message: [fina, dormi]


def test_dos_agentes_llaman_al_proveedor_en_paralelo_no_en_serie(mini_root):
    DELAY = 0.25
    fake = FakeProvider("fake", delay_s=DELAY)
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))
    _forzar_ambos_agentes(engine)

    r = engine.ask("inflación y cómo dormir mejor")

    assert len(fake.calls) == 2
    assert set(r["agentes"]) == {"fina", "dormi"}
    # Si fuera secuencial, las dos ventanas de tiempo no se solaparían en
    # absoluto (fin de la primera <= inicio de la segunda). En paralelo, el
    # inicio de la segunda ocurre ANTES de que termine la primera.
    (ini1, fin1), (ini2, fin2) = fake.call_windows
    solapan = ini2 < fin1 or ini1 < fin2
    assert solapan, f"las llamadas no se solaparon: {fake.call_windows}"


def test_un_solo_agente_pendiente_no_usa_threadpool_y_funciona_igual(mini_root):
    """Con 0 o 1 agente pendiente de LLM, `ask()` no crea ningún hilo — la
    ganancia de paralelizar solo existe con más de uno."""
    fake = citing_provider("fake")
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))

    r = engine.ask("cuál es la inflación en República Dominicana", agent_id="fina")

    assert len(fake.calls) == 1
    assert r["traza"]["fina"]["modo"] == "llm"


def test_resultados_paralelos_se_reportan_en_el_orden_original_de_agentes(mini_root):
    """El orden de `r['agentes']` debe reflejar el orden de enrutado, no el
    orden de finalización de los hilos (que no está garantizado)."""
    fake = FakeProvider("fake", delay_s=0.05)
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake))
    _forzar_ambos_agentes(engine)

    r = engine.ask("inflación y cómo dormir mejor")

    assert r["agentes"] == ["fina", "dormi"]


def test_traza_no_se_corrompe_con_escrituras_concurrentes(mini_root, tmp_path):
    """El lock alrededor de `trace_store.record` evita que dos hilos
    intercalen escrituras en el mismo archivo JSONL."""
    import json

    from orchestration.audit import JsonlTraceStore

    trace_dir = tmp_path / "traces"
    store = JsonlTraceStore(trace_dir)
    fake = FakeProvider("fake", delay_s=0.1)
    engine = MagnusEngine(mini_root, providers=_providers(mini_root, fake=fake),
                          trace_store=store)
    _forzar_ambos_agentes(engine)

    engine.ask("inflación y cómo dormir mejor")

    archivos = list(trace_dir.glob("*.jsonl"))
    assert len(archivos) == 1
    lineas = archivos[0].read_text(encoding="utf-8").strip().splitlines()
    # Si una escritura se hubiera intercalado con otra, alguna línea no
    # parsearía como JSON válido.
    for linea in lineas:
        json.loads(linea)
    agentes_trazados = {json.loads(l)["agente"] for l in lineas}
    assert agentes_trazados == {"fina", "dormi"}
