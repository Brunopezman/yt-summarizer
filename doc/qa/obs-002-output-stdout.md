# obs-002: `--output` imprime a stdout antes de escribir el archivo

- id: obs-002
- subtarea: cli-resumir
- severidad: no-bloqueante
- estado: abierta

## Descripción

En la rama `--output`, el `print(resumen)` (línea 272) ocurre **antes** del
`output.write_text(...)` (línea 276). Si la escritura del archivo falla
(p. ej. ruta no escribible), el comando termina con exit code 1 y mensaje a
stderr, pero stdout ya consumió el resumen: un consumidor que haga pipe de
stdout recibe contenido aunque el comando "falló".

No es un bug del contrato: el comportamiento documentado es "guardar además
de mostrar" y el test de fallo de escritura (`test_resumir_output_no_escribible_exit_1`)
pasa con exit 1 y mensaje claro. Queda anotado porque el orden
stdout-antes-de-write es razonable para el caso pipe (el resumen nunca se
pierde si el archivo falla), pero puede sorprender si se espera que exit code
1 implique "no hay salida de negocio".

## Evidencia

- Función: `yt_summarizer.cli.resumir` (líneas 272-278)
- Input: `resumir <url> --output <ruta-no-escribible>` con API key válida y transcripción/LLM OK
- Esperado vs obtenido: esperado exit 1 con mensaje claro a stderr (cumplido); anotación de que el resumen ya apareció en stdout antes del fallo de escritura
- Test / verificación: `tests/test_cli.py::test_resumir_output_no_escribible_exit_1` (línea 434) — verifica exit 1 y mensaje en stderr, pero no el estado de stdout (que ya contiene el resumen)

## Recomendación

Si se quiere el orden inverso (escribir archivo y recién después imprimir a
stdout), mover el `print(resumen)` después del bloque `if output is not None`,
para que un exit 1 por fallo de escritura no deje salida de negocio en stdout.
Evaluar si vale la pena antes de decidir; para uso con pipe el orden actual es
defendible.
