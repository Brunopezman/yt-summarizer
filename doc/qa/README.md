# Observaciones de QA

Registro escalable de observaciones que `qa` detecta durante las verificaciones y
que **no** ameritan un bug fix inmediato (no rompen el contrato de la subtarea),
pero merecen quedar constatadas para decisiones futuras.

## Formato

Cada observación es un archivo `obs-NNN-<slug>.md` (NNN correlativo de 3 dígitos).
Se crean siguiendo la plantilla `TEMPLATE.md`. El índice de abajo se actualiza
con cada alta.

Campos de la plantilla:

| Campo | Qué va |
|---|---|
| `id` | correlativo, `obs-NNN` |
| `subtarea` | nombre de la subtarea donde apareció (`<agente>-<feature>`) |
| `severidad` | `no-bloqueante` (no rompe el contrato) o `bloqueante` (debería haber frenado la entrega — solo si la verificación se aprobó igual) |
| `estado` | `abierta` → `aceptada` (se decide arreglar) / `descartada` (no se arregla) / `en-followup` |
| `descripcion` | qué se detectó y por qué no es bug del contrato |
| `evidencia` | función/input/esperado-obtenido o path de test que lo demuestra |
| `recomendacion` | qué se podría hacer al respecto |

## Índice

| ID | Subtarea | Severidad | Estado | Resumen |
|---|---|---|---|---|
| obs-001 | cli-resumir | no-bloqueante | abierta | Excepción inesperada de negocio escapa con traceback |
| obs-002 | cli-resumir | no-bloqueante | abierta | `--output` escribe después de imprimir a stdout |
