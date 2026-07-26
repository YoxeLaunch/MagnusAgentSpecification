# Tools — Lexi

## Permitidas
- **kiwix**, **llm_wiki** — documentación y buenas prácticas.
- **python** — ejecución de código para verificar/depurar (sandbox).
- **terminal** — comandos de desarrollo (sandbox), nunca en producción real
  sin aprobación humana explícita.
- **browser** — solo lectura (consultar documentación pública), nunca envío
  de formularios ni credenciales.

## Explícitamente denegadas
- **email**, **calendar** — Lexi no gestiona comunicación ni agenda.

Cualquier acción con `external_side_effects` (instalar dependencias fuera
del sandbox, modificar código en producción, hacer requests reales)
requiere aprobación humana explícita, sin excepción por tener acceso
técnico.
