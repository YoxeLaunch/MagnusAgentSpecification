"""Ejemplo minimalista (PoC) de Magnus Agent Engine.

Ejecutar sin claves API ni conexión externa (Modo Extractivo / Local-First):
    python -m examples.quickstart
"""
from __future__ import annotations

import sys

# Compatibilidad con consolas Windows (cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sdk import MagnusEngine


def main() -> None:
    # 1. Inicializar el motor Magnus apuntando a la raíz del proyecto
    engine = MagnusEngine(root=".")

    # 2. Consulta soberana multiagente (RAG Híbrido + Enrutado de Capacidades)
    pregunta = "¿Cómo organizo mi presupuesto y quiero invertir mi dinero?"
    print(f"🔍 Consulta: '{pregunta}'\n" + "-" * 55)

    resultado = engine.ask(pregunta)

    # 3. Presentar agente seleccionado, respuesta fundamentada y fuentes citadas
    agentes = resultado.get("agentes", [])
    if not agentes:
        print("⚠️ No se identificó dominio enrutado para la consulta.")
        return

    print(f"🤖 Agente Enrutado: {', '.join(agentes)}")
    print(f"💬 Respuesta Fundamentada:\n{resultado.get('respuesta')}\n")
    print(f"📚 Fuentes Citadas ({len(resultado.get('fuentes', []))}):")
    for f in resultado.get("fuentes", [])[:3]:
        print(f"  • {f}")


if __name__ == "__main__":
    main()
