"""Interfaz de línea de comandos (Typer) de yt-summarizer.

Capa de presentación del proyecto: traduce la entrada del usuario (URL y
flags) en llamadas a las capas de negocio (:func:`get_transcript` y
:meth:`Summarizer.summarize`), sin reimplementar ninguna de esas lógicas.

Invocation::

    python -m yt_summarizer.cli resumir <url>
    yt-summarizer resumir <url>

Convenciones de salida (``cli-ux-patterns``):

- el resumen es la única salida de negocio: va a **stdout** (pipeable);
- el progreso, las confirmaciones y los errores van a **stderr**;
- errores de uso (URL o flag inválido) → exit code 2;
- errores de negocio/configuración (transcripción, LLM, API keys) →
  exit code 1, siempre con mensaje claro en español y nunca un traceback
  crudo.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final, NoReturn, Optional
from urllib.parse import parse_qs, urlsplit

import typer

from yt_summarizer.config import (
    ConfigError,
    get_api_key,
    get_provider,
)
from yt_summarizer.summarizer import LLMProviderError, get_summarizer
from yt_summarizer.transcript import TranscriptError, get_transcript

try:
    from rich.console import Console
except ImportError:  # pragma: no cover - rich es opcional
    Console = None

__all__ = [
    "app",
    "Largo",
    "Proveedor",
    "InvalidVideoURLError",
    "extract_video_id",
    "resumir",
]

#: ID de video de YouTube: exactamente 11 caracteres alfanuméricos, `-` o `_`.
_VIDEO_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: Extensiones de host aceptadas para URLs de YouTube.
_HOSTS_VALIDOS: Final = ("youtube.com", "youtu.be", "youtube-nocookie.com")

#: Rutas de YouTube que llevan el ID como segundo segmento.
_PATHS_CON_ID: Final = ("shorts", "embed", "live")


class Largo(str, enum.Enum):
    """Niveles de detalle del resumen soportados por ``--largo``."""

    corto = "corto"
    medio = "medio"
    extenso = "extenso"


class Proveedor(str, enum.Enum):
    """Proveedores LLM soportados por ``--proveedor``."""

    openai = "openai"
    gemini = "gemini"


class InvalidVideoURLError(ValueError):
    """La URL/ID provisto no permite extraer un ID de video de YouTube válido."""


def extract_video_id(url: str) -> str:
    """Extrae el ID de video de YouTube desde una URL (o un ID crudo).

    Formatos soportados:

    - ``https://www.youtube.com/watch?v=ID`` (con o sin query params extra);
    - ``https://youtu.be/ID``;
    - ``https://www.youtube.com/shorts/ID``;
    - ``https://www.youtube.com/embed/ID``;
    - ``https://www.youtube.com/live/ID``;
    - un ID crudo de 11 caracteres (sin URL).

    El esquema es opcional (``youtube.com/watch?v=ID`` funciona igual) y las
    URLs pueden traer query params y fragmentos extra. Se aceptan subdominios
    de YouTube (``m.``, ``music.``, etc.).

    Args:
        url: URL de YouTube o ID de video.

    Returns:
        El ID del video (11 caracteres).

    Raises:
        InvalidVideoURLError: si no se pudo extraer un ID válido.
    """
    texto = url.strip()
    # Un ID crudo ya es suficiente.
    if _VIDEO_ID_RE.fullmatch(texto):
        return texto

    # Normalizar: sin esquema, urlsplit igual funciona.
    if "://" not in texto:
        texto = "https://" + texto
    partes = urlsplit(texto)
    host = (partes.hostname or "").lower().rstrip(".")
    if not any(host == h or host.endswith("." + h) for h in _HOSTS_VALIDOS):
        raise InvalidVideoURLError(
            f"No se pudo extraer un ID de video de '{url}': no es una URL de "
            f"YouTube ({', '.join(_HOSTS_VALIDOS)}) ni un ID de 11 caracteres."
        )

    candidatos: list[str] = []
    if host == "youtu.be" or host.endswith(".youtu.be"):
        primer_segmento = partes.path.strip("/").split("/", 1)[0]
        if primer_segmento:
            candidatos.append(primer_segmento)
    else:
        # youtube.com: el parámetro ?v= vale para watch y cualquier ruta.
        candidatos.extend(parse_qs(partes.query).get("v", []))
        # Rutas tipo /shorts/ID, /embed/ID, /live/ID.
        segmentos = [s for s in partes.path.split("/") if s]
        if len(segmentos) >= 2 and segmentos[0] in _PATHS_CON_ID:
            candidatos.append(segmentos[1])

    for candidato in candidatos:
        if _VIDEO_ID_RE.fullmatch(candidato):
            return candidato

    raise InvalidVideoURLError(
        f"No se pudo extraer un ID de video válido de '{url}'. Formatos "
        "aceptados: youtube.com/watch?v=ID, youtu.be/ID, "
        "youtube.com/shorts/ID, youtube.com/embed/ID o un ID crudo de "
        "11 caracteres."
    )


#: Consola de stderr con color, si rich está disponible; si no, ``None``.
_console = Console(stderr=True) if Console is not None else None


@contextmanager
def _status(message: str) -> Iterator[None]:
    """Indicador de progreso durante una operación larga (a stderr).

    Con rich muestra un spinner; sin rich, un mensaje plano de ``→``.
    """
    if _console is not None:
        with _console.status(message, spinner="dots"):
            yield
    else:
        typer.echo(f"→ {message}", err=True)
        yield


def _error(message: str) -> None:
    """Imprime un error claro a stderr (en rojo si rich está disponible)."""
    if _console is not None:
        _console.print(f"[bold red]Error:[/bold red] {message}")
    else:
        typer.echo(f"Error: {message}", err=True)


def _fail(message: str, code: int = 1) -> NoReturn:
    """Imprime un error a stderr y aborta la ejecución con exit code no nulo."""
    _error(message)
    raise typer.Exit(code=code)


app = typer.Typer(
    name="yt-summarizer",
    help="Resumí la transcripción de un video de YouTube con IA (OpenAI o Gemini).",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """yt-summarizer: descarga transcripciones de YouTube y las resume con IA."""


@app.command()
def resumir(
    url: Annotated[
        str,
        typer.Argument(
            help="URL del video de YouTube (watch?v=, youtu.be/, /shorts/, "
            "/embed/, /live/) o ID crudo de 11 caracteres."
        ),
    ],
    idioma: Annotated[
        str,
        typer.Option(
            "--idioma",
            "-i",
            help="Idioma de la transcripción (código ISO 639-1, ej. 'es', 'en').",
        ),
    ] = "es",
    largo: Annotated[
        Largo,
        typer.Option(
            "--largo",
            "-l",
            help="Nivel de detalle del resumen.",
        ),
    ] = Largo.medio,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Guardar el resumen en este archivo, además de mostrarlo.",
        ),
    ] = None,
    proveedor: Annotated[
        Optional[Proveedor],
        typer.Option(
            "--proveedor",
            "-p",
            help="Proveedor LLM. Default: el de LLM_PROVIDER en .env.",
        ),
    ] = None,
) -> None:
    """Descarga la transcripción de un video de YouTube y la resume con IA."""
    # 1) Parseo de la URL → video_id (error de uso, exit code 2).
    try:
        video_id = extract_video_id(url)
    except InvalidVideoURLError as exc:
        _fail(str(exc), code=2)

    # 2) Fail-fast de configuración: resolver proveedor y validar su API key
    #    ANTES de tocar red (transcripción o LLM).
    try:
        proveedor_nombre = get_provider(proveedor.value if proveedor else None)
        get_api_key(proveedor_nombre)
    except ConfigError as exc:
        _fail(str(exc))

    # 3) Pipeline de negocio: transcripción → resumen.
    try:
        with _status(
            f"Descargando transcripción del video '{video_id}' en idioma "
            f"'{idioma}'..."
        ):
            texto = get_transcript(video_id, lang=idioma)
        with _status(
            f"Generando resumen con {proveedor_nombre} (largo: {largo.value})..."
        ):
            resumen = get_summarizer(provider=proveedor_nombre).summarize(
                texto, length=largo.value
            )
    except TranscriptError as exc:
        _fail(str(exc))
    except LLMProviderError as exc:
        _fail(str(exc))

    # 4) Salida: el resumen es la única salida de negocio → stdout.
    print(resumen)

    if output is not None:
        try:
            output.write_text(resumen + "\n", encoding="utf-8")
        except OSError as exc:
            _fail(f"No se pudo escribir el resumen en '{output}': {exc}")
        typer.echo(f"Resumen guardado en '{output}'.", err=True)
    else:
        typer.echo(
            "Usá --output <archivo> para guardar el resumen en un archivo.",
            err=True,
        )


if __name__ == "__main__":
    app()
