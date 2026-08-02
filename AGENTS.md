# AGENTS.md — yt-summarizer

Convenciones de la arquitectura agéntica de este proyecto. Ver `agentes/` para cada rol y `command/loop-until-green.md` para el flujo de ejecución.

## Agentes

| Archivo | Rol |
|---|---|
| `agentes/orquestador.md` | `mode: primary`, único con `permission.task`. Divide, delega, verifica, escala. |
| `agentes/integration-dev.md` | Transcripción + integración LLM (`transcript.py`, `summarizer.py`) |
| `agentes/cli-dev.md` | Interfaz Typer + config (`cli.py`, `config.py`) |
| `agentes/qa.md` | Tests, sin tocar código de producción (`tests/`) |

## Protocolo de loop (aplica a todos los subagentes)

Ningún agente entrega en una sola pasada sin verificación. El ciclo estándar es:

1. **Plan**: el orchestrator delega con contrato explícito (firma esperada, contexto previo si es reintento).
2. **Acción**: el agente dueño implementa dentro de sus archivos permitidos.
3. **Verificación**: `qa` valida contra el contrato. No se marca "listo" sin pasar por acá.
4. **Reflexión / reintento**: si falla, el reporte de `qa` se suma al estado de la subtarea (`.agent-state/<subtarea>.md`) y se vuelve al paso 2 con ese contexto — nunca un reintento a ciegas.
5. **Salida**: la subtarea cierra por **éxito** (qa aprueba) o por **escalamiento** (tope de 3 intentos, o mismo error dos veces seguidas). No hay una tercera opción — el loop no puede quedar abierto indefinidamente.

Detalle completo del ciclo, logging y formato de estado: `command/loop-until-green.md` y `.agent-state/TEMPLATE.md`.

## Reglas globales

- Los límites de escritura de cada subagente son disjuntos (ver tabla de arriba) — permite paralelizar sin pisarse, excepto cuando hay dependencia de firma no cerrada (ver `agentes/orquestador.md` § Reglas de conflicto).
- `qa` nunca corrige código de producción, solo reporta.
- Todo intento de loop queda registrado en `.agent-state/loop.log` — no se descarta el historial hasta que la subtarea cierra.
