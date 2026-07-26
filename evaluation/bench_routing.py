#!/usr/bin/env python
"""Banco de ENRUTADO: mide cobertura, precisión y falsos positivos del
Capability Engine, comparando léxico solo vs. el híbrido (paso 6 del ROADMAP).

Distinto de `bench_retrieval.py` (¿el retriever encuentra la nota correcta?):
aquí se mide ¿el motor completo (`CapabilityEngine` → `AgentSelectionEngine`)
enruta la consulta al AGENTE correcto, usando los agentes y capacidades
REALES del repositorio? No usa red ni credenciales: solo enrutado.

Definiciones (sobre `evaluation/goldens/routing.yaml`):
  - COBERTURA: de las consultas con dominio esperado (`esperado` no vacío),
    ¿cuántas encontraron AL MENOS un agente? (no importa si es el correcto)
  - PRECISIÓN: de las que encontraron algo, ¿cuántas encontraron un agente
    que sí está en `esperado`?
  - FALSOS POSITIVOS: de las consultas marcadas `sin_dominio` (`esperado`
    vacío — ningún agente debería seleccionarse), ¿cuántas SÍ enrutaron a
    alguno? Es la métrica que un enrutado "que adivina" dispararía.

Uso:
    python -m evaluation.bench_routing [--goldens ruta.yaml]

El híbrido (`CapabilityEngine` por defecto) se compara contra el léxico solo
(`matcher=LexicalCapabilityMatcher(catalog)` explícito) — nunca contra un
número inventado. Exit code != 0 si el híbrido empeora la precisión o
aumenta los falsos positivos frente al léxico.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from orchestration.capability.matcher import LexicalCapabilityMatcher  # noqa: E402
from orchestration.capability_engine import CapabilityEngine  # noqa: E402
from orchestration.registry.agent_registry import AgentRegistry  # noqa: E402
from orchestration.registry.capability_catalog import CapabilityCatalog  # noqa: E402


@dataclass
class Caso:
    consulta: str
    categoria: str
    esperado: list[str]

    @property
    def es_sin_dominio(self) -> bool:
        return not self.esperado


def cargar_casos(path: Path) -> list[Caso]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Caso(c["consulta"], c.get("categoria", "?"), list(c.get("esperado", [])))
            for c in raw.get("casos", [])]


@dataclass
class Resultado:
    nombre: str
    cobertura: float
    precision: float
    falsos_positivos: int
    total_sin_dominio: int
    detalle_fallos: list[str]
    por_categoria: dict[str, tuple[int, int]]  # categoria -> (aciertos, total)


def evaluar(nombre: str, engine: CapabilityEngine, casos: list[Caso]) -> Resultado:
    con_dominio = [c for c in casos if not c.es_sin_dominio]
    sin_dominio = [c for c in casos if c.es_sin_dominio]

    encontrados = 0
    aciertos = 0
    fallos: list[str] = []
    por_categoria: dict[str, list[int]] = {}

    for caso in con_dominio:
        agentes = [a.id for a in engine.route_to_agents(caso.consulta, k=3)]
        cat = por_categoria.setdefault(caso.categoria, [0, 0])
        cat[1] += 1
        if agentes:
            encontrados += 1
            if set(agentes) & set(caso.esperado):
                aciertos += 1
                cat[0] += 1
            else:
                fallos.append(f"[{caso.categoria}] '{caso.consulta}' → {agentes} "
                              f"(esperaba uno de {caso.esperado})")
        else:
            fallos.append(f"[{caso.categoria}] '{caso.consulta}' → sin_dominio "
                          f"(esperaba uno de {caso.esperado})")

    falsos_positivos = 0
    for caso in sin_dominio:
        agentes = [a.id for a in engine.route_to_agents(caso.consulta, k=3)]
        cat = por_categoria.setdefault(caso.categoria, [0, 0])
        cat[1] += 1
        if agentes:
            falsos_positivos += 1
            fallos.append(f"[sin_dominio] '{caso.consulta}' → {agentes} (debía quedar sin agente)")
        else:
            cat[0] += 1

    cobertura = encontrados / len(con_dominio) if con_dominio else 1.0
    precision = aciertos / encontrados if encontrados else 0.0
    return Resultado(nombre, cobertura, precision, falsos_positivos, len(sin_dominio),
                     fallos, {k: tuple(v) for k, v in por_categoria.items()})


def _build_engine(root: Path, matcher=None) -> CapabilityEngine:
    caps = CapabilityCatalog(root / "capabilities").load_all()
    registry = AgentRegistry(
        root / "agents", capabilities=caps,
        models_yaml=root / "configs" / "models.yaml",
        permissions_yaml=root / "configs" / "permissions.yaml",
        mcp_catalog_yaml=root / "tools" / "mcp_catalog.yaml")
    registry.load_all()
    return CapabilityEngine(caps, registry, matcher=matcher(caps) if matcher else None)


def _imprimir(r: Resultado) -> None:
    print(f"  {r.nombre}")
    print(f"    cobertura           = {r.cobertura:.1%}")
    print(f"    precisión           = {r.precision:.1%}")
    print(f"    falsos positivos    = {r.falsos_positivos}/{r.total_sin_dominio}")
    for cat, (ok, total) in sorted(r.por_categoria.items()):
        print(f"      · {cat:<24} {ok}/{total}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Banco de enrutado de Magnus")
    p.add_argument("--goldens", default=str(ROOT / "evaluation" / "goldens" / "routing.yaml"))
    args = p.parse_args(argv)

    casos = cargar_casos(Path(args.goldens))
    if not casos:
        print("no hay casos que evaluar", file=sys.stderr)
        return 1

    con_dominio = sum(1 for c in casos if not c.es_sin_dominio)
    sin_dominio = sum(1 for c in casos if c.es_sin_dominio)
    print(f"casos: {len(casos)} ({con_dominio} con dominio esperado, "
          f"{sin_dominio} sin dominio)\n")

    lexico = evaluar("léxico solo (baseline)",
                     _build_engine(ROOT, matcher=LexicalCapabilityMatcher), casos)
    hibrido = evaluar("híbrido léxico+vectorial (ahora, default de CapabilityEngine)",
                      _build_engine(ROOT), casos)

    _imprimir(lexico)
    print()
    _imprimir(hibrido)

    print(f"\n  delta cobertura híbrido vs léxico:        {hibrido.cobertura - lexico.cobertura:+.1%}")
    print(f"  delta precisión híbrido vs léxico:        {hibrido.precision - lexico.precision:+.1%}")
    print(f"  delta falsos positivos híbrido vs léxico: "
          f"{hibrido.falsos_positivos - lexico.falsos_positivos:+d}")

    if hibrido.detalle_fallos:
        print(f"\n  casos que el híbrido sigue sin resolver bien ({len(hibrido.detalle_fallos)}):")
        for f in hibrido.detalle_fallos:
            print(f"    · {f}")

    regresion = (hibrido.precision < lexico.precision or
                hibrido.falsos_positivos > lexico.falsos_positivos)
    if regresion:
        print("\n  FALLO: el híbrido empeora la precisión o aumenta los falsos "
              "positivos frente al léxico.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
