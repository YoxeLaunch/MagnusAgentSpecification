# Knowledge — Ernesto Libras

## Namespaces por defecto (LLM Wiki)
- `01-Economia-y-Finanzas/` — única parcela de conocimiento consultada por
  defecto (alineado con `agent.yaml::knowledge.sources`).

## Justificación
Ernesto solo ve su parcela: mínimo privilegio (constitución, principio 5). Si
en el futuro se agregan namespaces especializados (`imf/`, `world_bank/`,
`federal_reserve/`) como subcarpetas independientes de la wiki, deben
añadirse aquí y en `agent.yaml` explícitamente — nunca por defecto amplio
(`*`).

## Retrieval
`top_k: 8`, `min_score: 0.35`, reranker activado — prioriza precisión sobre
cobertura; una proyección económica mal fundamentada es peor que una
declaración explícita de falta de evidencia.
