"""Evaluación de respuestas — Componente 9 (paso 3 del ROADMAP).

Dos piezas independientes que el motor compone:

  - `citation_evaluator`: ¿la respuesta se apoya en la evidencia que se le dio?
  - `guardrails`: ¿la respuesta pertenece a un dominio con límites propios
    (salud, finanzas) o la consulta contiene una señal de urgencia?

Son cosas distintas a propósito: una cita válida no vuelve segura una
recomendación inapropiada, y una consulta de urgencia no se arregla citando
mejor.
"""
from .citation_evaluator import (
    CitationEvaluator, EvaluationResult, Evaluator,
)
from .guardrails import GuardrailCheck, Guardrails

__all__ = [
    "CitationEvaluator", "EvaluationResult", "Evaluator",
    "GuardrailCheck", "Guardrails",
]
