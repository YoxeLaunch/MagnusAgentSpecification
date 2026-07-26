# Tools — Ernesto Libras

## Permitidas
- **kiwix** — consulta de fuentes offline curadas.
- **llm_wiki** — acceso de solo lectura a la LLM Wiki (vía RAG, nunca directo).
- **magnus_capital** — herramienta interna de cálculo financiero.
- **python** — cálculo numérico (proyecciones, escenarios), sin acceso a red.
- **imf_api**, **world_bank_api**, **fred** — series de datos económicos
  públicos, solo lectura.

## Explícitamente denegadas
- **terminal** — no necesita ejecutar comandos del sistema.
- **email**, **calendar** — no envía comunicaciones ni gestiona agenda.
- **browser** — no navega la web abierta; su conocimiento pasa por la wiki
  curada, no por búsquedas no verificadas.

Cualquier acción con efecto externo (aunque estuviera permitida) requiere
aprobación humana (`permissions.yaml::economist_readonly.actions`).
