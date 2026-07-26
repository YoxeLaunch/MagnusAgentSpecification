# Knowledge — Serena

## Namespaces por defecto
- `03-Salud-Mental-y-Desarrollo-Personal/` — única parcela consultada por
  defecto.

## Justificación
Mínimo privilegio: Serena no necesita ver `01-Economia-y-Finanzas/` ni
`02-Salud-Corporal/` para cumplir su misión. Si un caso requiere colaboración
con otro agente (p. ej. Dr. Soma para la dimensión física del estrés), la
colaboración ocurre a nivel de Router (multiagente), no ampliando el scope
de conocimiento de Serena.

## Retrieval
`top_k: 8`, `min_score: 0.30` — umbral algo más bajo que Ernesto porque el
lenguaje de bienestar admite más variación léxica que el económico; se
compensa con reranker activado.
