# Permissions — Ernesto Libras

Referencia: `configs/permissions.yaml::policies.economist_readonly`.

- **Lectura de conocimiento:** `economics/`, `finance/`, `imf/`,
  `world_bank/`, `federal_reserve/`, `oecd/` (según se incorporen como
  namespaces reales de la wiki).
- **Escritura de conocimiento:** ninguna. Ernesto nunca escribe directamente
  en `knowledge/` — solo puede proponer vía `memory.semantic_write:
  proposal_only`.
- **Acciones con efecto externo:** requieren aprobación humana explícita
  (`external_side_effects: require_human_approval`).
