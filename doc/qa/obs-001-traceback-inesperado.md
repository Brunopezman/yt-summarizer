# obs-001: Excepción inesperada de negocio escapa con traceback crudo

- id: obs-001
- subtarea: cli-resumir
- severidad: no-bloqueante
- estado: aceptada

## Descripción

El bloque `try/except` de `resumir` captura únicamente `TranscriptError`,
`LLMProviderError` y `ConfigError` (los tipos de error del contrato de la
subtarea). Cualquier otra excepción que escapen de `get_transcript` o de
`summarize` (p. ej. un `ValueError`, `TypeError` o `RuntimeError` de una capa
de negocio) no se captura y escapa hasta Typer/Click, que en terminal real
imprime el traceback crudo.

No es un bug del contrato: todos los tipos de error definidos en el contrato
(`TranscriptError` / `LLMProviderError` / `ConfigError`) sí se capturan y
producen mensaje claro a stderr con exit code 1. La observación queda por la
convención del propio módulo ("nunca un traceback crudo", docstring líneas
14-19) y por robustez ante excepciones inesperadas de la capa de negocio.

## Evidencia

- Función: `yt_summarizer.cli.resumir` (bloque try/except, líneas 254-269)
- Input: cualquier URL/ID válido donde `get_transcript` o `summarize` lance una excepción fuera del contrato (p. ej. `ValueError`)
- Esperado vs obtenido: esperado un mensaje claro a stderr sin traceback; obtenido que la excepción no se captura y Typer/Click la imprime con traceback crudo
- Test / verificación: `tests/test_cli.py` — los tests de error existentes solo ejercitan los tipos del contrato (`test_resumir_sin_transcripcion_exit_1_mensaje_claro`, `test_resumir_rate_limit_llm_exit_1_mensaje_distinto`, `test_resumir_error_generico_llm_exit_1`); no hay test que fuerce una excepción inesperada desde la capa de negocio

## Recomendación

Si se quiere cerrar el hueco, agregar un `except Exception` final (después de
los tres `except` de contrato) que llame `_fail("Error inesperado: ...")`, o
mejor, hacer que la capa de negocio envuelva siempre sus errores en los tipos
del contrato. No es urgente: requiere un bug real en la capa de negocio para
dispararse.

## Resolución

Cerrada en reintento de mejora. En `yt_summarizer/cli.py`, `resumir`, se agregó
un `except Exception` final (después de `except TranscriptError` /
`except LLMProviderError`) en el bloque de negocio (líneas 270-278) que
traduce cualquier excepción inesperada de la capa de negocio a un mensaje
claro en español a stderr con exit code 1 y sin traceback crudo, incluyendo el
detalle de la excepción de forma breve en el mensaje para poder debuggear.

Verificación manual: `get_transcript` mockeado lanzando `ValueError` →
`exit_code=1`, stderr = "Error inesperado al procesar el video 'dQw4w9WgXcQ':
id malformado internamente: 1234. ...", `stdout=""`, sin "Traceback" en stderr,
`result.exception` es `SystemExit` (mecanismo normal de exit de Typer).
