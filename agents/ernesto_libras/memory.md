# Memory — Ernesto Libras

| Tipo | Uso |
|---|---|
| Short term | Contexto de la conversación/sesión actual (cifras, supuestos ya discutidos). |
| Episodic | Traza de qué proyecciones se entregaron y con qué evidencia — permite auditar decisiones pasadas. |
| Semantic | Solo `proposal_only`: si detecta un patrón recurrente en preguntas de usuarios que sugiere un hueco de conocimiento (p. ej. falta de datos sobre un país), lo propone — nunca lo escribe directamente. |
| Long term / Project Memory | No la usa por defecto; un análisis económico puntual rara vez necesita continuidad de proyecto. Si un caso de uso futuro lo requiere (seguimiento de una cartera a lo largo de meses), debe activarse explícitamente aquí y en `agent.yaml`. |
