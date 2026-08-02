"""Descarga de transcripciones de YouTube.

Capa de datos del proyecto: expone ``get_transcript()``, que obtiene el texto
plano de una transcripción de YouTube a partir del ID de un video.

Los errores propios de esta capa se agrupan bajo :class:`TranscriptError`:

- :class:`TranscriptNotFoundError` — el video no tiene transcripción disponible
  o el idioma pedido no existe para ese video.
- :class:`TranscriptsDisabledError` — el canal tiene las transcripciones
  deshabilitadas.

Las excepciones crudas de ``youtube-transcript-api`` nunca se propagan al
llamador: se traducen a estas excepciones propias y tipadas.

Ejemplo de uso::

    from yt_summarizer.transcript import get_transcript

    texto = get_transcript("UF8uR6Z6KLc", lang="es")
"""

from __future__ import annotations

from typing import Any, Iterable

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    TranscriptsDisabled as _YTTranscriptsDisabled,
    YouTubeTranscriptApiException,
)

__all__ = [
    "get_transcript",
    "TranscriptError",
    "TranscriptNotFoundError",
    "TranscriptsDisabledError",
]


class TranscriptError(Exception):
    """Error base de la capa de transcripciones de YouTube.

    Permite al llamador capturar cualquiera de los errores de esta capa con un
    solo ``except TranscriptError``.
    """


class TranscriptNotFoundError(TranscriptError):
    """El video no tiene transcripción disponible o el idioma pedido no existe.

    Cubre dos casos de uso:

    - el video existe pero no expone ninguna transcripción;
    - el video tiene transcripciones, pero ninguna en el idioma pedido.
    """


class TranscriptsDisabledError(TranscriptError):
    """El canal del video tiene las transcripciones deshabilitadas."""


def _snippet_text(snippet: Any) -> str:
    """Extrae el texto de un segmento de transcripción.

    Compatible con los dos formatos que devuelve ``youtube-transcript-api``
    según la versión instalada: dicts ``{"text": ...}`` (v0.x) y objetos
    ``FetchedTranscriptSnippet`` con atributo ``.text`` (v1.x).
    """
    if isinstance(snippet, dict):
        return snippet.get("text", "")
    return getattr(snippet, "text", "")


def _list_transcripts(api: YouTubeTranscriptApi, video_id: str) -> Any:
    """Devuelve el ``TranscriptList`` del video.

    ``youtube-transcript-api`` renombró ``list_transcripts()`` a ``list()`` en
    la v1.x. Este helper usa la que esté disponible en la versión instalada.
    """
    list_method = getattr(api, "list", None) or getattr(api, "list_transcripts")
    return list_method(video_id)


def get_transcript(video_id: str, lang: str = "es") -> str:
    """Devuelve el texto plano de la transcripción de un video de YouTube.

    El texto es el resultado de concatenar los segmentos de la transcripción
    con un salto de línea entre cada uno, en orden cronológico.

    Args:
        video_id: ID del video (no la URL completa).
        lang: código de idioma ISO 639-1 (ej. ``"es"``, ``"en"``). Por defecto
            ``"es"``.

    Returns:
        El texto plano de la transcripción.

    Raises:
        TranscriptNotFoundError: si el video no tiene transcripción disponible
            o el idioma pedido no existe para ese video.
        TranscriptsDisabledError: si el canal tiene las transcripciones
            deshabilitadas.

    Ejemplo::

        from yt_summarizer.transcript import get_transcript

        texto = get_transcript("UF8uR6Z6KLc", lang="es")
    """
    api = YouTubeTranscriptApi()
    try:
        transcript_list = _list_transcripts(api, video_id)
        transcript = transcript_list.find_transcript([lang])
        fetched = transcript.fetch()
    except _YTTranscriptsDisabled as exc:
        # La API avisa explícitamente que el canal deshabilitó las transcripciones.
        raise TranscriptsDisabledError(
            f"Las transcripciones del video '{video_id}' están deshabilitadas "
            "por el canal."
        ) from exc
    except (CouldNotRetrieveTranscript, YouTubeTranscriptApiException) as exc:
        # NoTranscriptFound cubre "sin transcripción" y "idioma inexistente";
        # VideoUnavailable/VideoUnplayable/YouTubeRequestFailed y el resto de la
        # familia también significan que no se pudo obtener una transcripción.
        raise TranscriptNotFoundError(
            f"No se pudo obtener una transcripción para el video '{video_id}' "
            f"en el idioma '{lang}'."
        ) from exc

    segments: Iterable[Any] = fetched
    return "\n".join(_snippet_text(segment) for segment in segments)
