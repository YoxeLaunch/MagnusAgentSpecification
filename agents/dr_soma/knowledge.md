# Knowledge — Dr. Soma

## Namespaces por defecto
- `02-Salud-Corporal/` — única parcela consultada por defecto.

## Justificación
Mínimo privilegio: educación de estilo de vida, no diagnóstico, así que no
necesita ver otros namespaces.

## Retrieval
`top_k: 8`, `min_score: 0.30`, reranker activado — el umbral algo
conservador ayuda a evitar que se recuperen pasajes ambiguos que podrían
malinterpretarse como consejo médico específico.
