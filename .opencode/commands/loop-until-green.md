---
description: Ejecuta el ciclo delegar → verificar → reintentar con contexto para una subtarea, hasta que qa la apruebe o se alcance el tope de intentos.
---

# /loop-until-green <subtarea>

Flujo que corre el orchestrator para cerrar una subtarea sin iterar a ciegas.

1. **Leer o crear estado**: abrir `agent-state/<subtarea>.md`. Si no existe, crearlo desde `agent-state/TEMPLATE.md` con intento = 1.
2. **Delegar**: invocar al agente dueño (según tabla de ruteo de `orquestador.md`) pasando el contrato esperado y, si `intento > 1`, el contenido completo de `agent-state/<subtarea>.md`.
3. **Verificar**: invocar a `qa` sobre lo entregado.
4. **Evaluar resultado**:
   - Si `qa` aprueba → loguear en `agent-state/loop.log` (`<subtarea> | intento <N> | ok`), borrar el estado de la subtarea, listo.
   - Si `qa` reporta falla:
     - `intento += 1`
     - actualizar `agent-state/<subtarea>.md` con el reporte de `qa` (función, input, esperado vs obtenido) y el enfoque que se descartó
     - loguear en `agent-state/loop.log` (`<subtarea> | intento <N> | falla | <resumen de una línea>`)
     - si `intento > 3` **o** el error es idéntico al de la iteración anterior dos veces seguidas → cortar el loop, reportar a Bruno con el historial completo, salir
     - si no → volver al paso 2

## Cuándo usarlo

Cualquier subtarea delegada a `integration-dev` o `cli-dev` que requiera pasar por `qa` antes de cerrarse. No usar para tareas puramente de investigación/lectura (esas no tienen loop de verificación, terminan en un solo paso).
