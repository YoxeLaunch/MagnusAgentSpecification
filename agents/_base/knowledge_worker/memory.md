## Heredado de KnowledgeWorker (política base de memoria)

| Tipo | Política base |
|---|---|
| Short term | Activo por defecto: contexto de la sesión actual. |
| Episodic | Activo por defecto: traza de qué se respondió y con qué evidencia. |
| Semantic | Solo `proposal_only` — nunca escritura directa a `knowledge/`. |

Un agente concreto puede añadir Project Memory si su caso de uso lo requiere
(ver su propio `memory.md` para la política específica).
