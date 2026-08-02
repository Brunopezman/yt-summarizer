"""Tests de la capa de transcripción (`yt_summarizer.transcript`).

Todos los tests mockean el acceso a YouTube: nunca se pega a la API real.
Se patchea `yt_summarizer.transcript._list_transcripts` (el único punto de
entrada a la librería dentro de `get_transcript`) para simular cada caso de la
jerarquía de errores de `youtube-transcript-api`, incluida la trampa real del
catch-order: `TranscriptsDisabled` es subclase de `CouldNotRetrieveTranscript`,
así que cada excepción cruda se mockea por separado para verificar que se
traduce a la excepción propia correcta.
"""

from types import SimpleNamespace

import pytest
from requests import HTTPError
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
    YouTubeTranscriptApiException,
)

import yt_summarizer.transcript as transcript_module
from yt_summarizer.transcript import (
    TranscriptError,
    TranscriptNotFoundError,
    TranscriptsDisabledError,
    _list_transcripts,
    get_transcript,
)

VIDEO_ID = "ABC123"
LANG_ES = "es"


class _FakeTranscript:
    """Simula un `Transcript` de youtube-transcript-api."""

    def __init__(self, snippets, fetch_error=None):
        self._snippets = snippets
        self._fetch_error = fetch_error

    def fetch(self):
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._snippets


class _FakeTranscriptList:
    """Simula un `TranscriptList` de youtube-transcript-api.

    Registra los idiomas pedidos en `find_transcript` y puede configurarse para
    lanzar una excepción cruda (sea en el listado o en la descarga).
    """

    def __init__(self, snippets=None, find_error=None, fetch_error=None):
        self._snippets = snippets
        self._find_error = find_error
        self._fetch_error = fetch_error
        self.find_calls = []

    def find_transcript(self, langs):
        self.find_calls.append(list(langs))
        if self._find_error is not None:
            raise self._find_error
        return _FakeTranscript(self._snippets, fetch_error=self._fetch_error)


@pytest.fixture
def fake_transcript_list(monkeypatch):
    """Instala un `_FakeTranscriptList` como resultado de listar transcripciones.

    Devuelve una factory que configura el caso (snippets y/o error) y permite
    inspeccionar el fake (p. ej. `find_calls`) desde el test.
    """

    def _install(snippets=None, find_error=None, fetch_error=None):
        fake = _FakeTranscriptList(
            snippets=snippets,
            find_error=find_error,
            fetch_error=fetch_error,
        )
        monkeypatch.setattr(
            transcript_module, "_list_transcripts", lambda api, video_id: fake
        )
        return fake

    return _install


def _no_transcript_found(langs):
    return NoTranscriptFound(VIDEO_ID, langs, _FakeTranscriptList())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_transcript_devuelve_texto_plano_concatenado_con_saltos_de_linea(
    fake_transcript_list,
):
    snippets = [
        {"text": "Hola mundo"},
        {"text": "Segunda linea"},
        {"text": "Tercera linea"},
    ]
    fake_transcript_list(snippets=snippets)

    texto = get_transcript(VIDEO_ID)

    assert texto == "Hola mundo\nSegunda linea\nTercera linea"


def test_get_transcript_acepta_snippets_con_atributo_text_formato_v1(
    fake_transcript_list,
):
    # youtube-transcript-api v1.x devuelve objetos FetchedTranscriptSnippet.
    snippets = [SimpleNamespace(text="Primera"), SimpleNamespace(text="Segunda")]
    fake_transcript_list(snippets=snippets)

    texto = get_transcript(VIDEO_ID)

    assert texto == "Primera\nSegunda"


def test_get_transcript_transcripcion_vacia_devuelve_string_vacio(
    fake_transcript_list,
):
    fake_transcript_list(snippets=[])

    assert get_transcript(VIDEO_ID) == ""


def test_get_transcript_pasa_el_idioma_pedido_a_find_transcript(
    fake_transcript_list,
):
    fake = fake_transcript_list(snippets=[{"text": "x"}])

    get_transcript(VIDEO_ID, lang="fr")

    assert fake.find_calls == [["fr"]]


def test_get_transcript_usa_es_por_defecto_cuando_no_se_pasa_idioma(
    fake_transcript_list,
):
    fake = fake_transcript_list(snippets=[{"text": "x"}])

    get_transcript(VIDEO_ID)

    assert fake.find_calls == [["es"]]


# ---------------------------------------------------------------------------
# Casos de error: video sin transcripción / idioma inexistente
# ---------------------------------------------------------------------------


def test_get_transcript_sin_transcripcion_lanza_transcript_not_found(
    fake_transcript_list,
):
    fake_transcript_list(find_error=_no_transcript_found([LANG_ES]))

    with pytest.raises(TranscriptNotFoundError) as exc:
        get_transcript(VIDEO_ID)

    assert VIDEO_ID in str(exc.value)
    assert LANG_ES in str(exc.value)
    # La excepción cruda se conserva como causa (raise ... from exc).
    assert isinstance(exc.value.__cause__, NoTranscriptFound)


def test_get_transcript_idioma_inexistente_lanza_transcript_not_found(
    fake_transcript_list,
):
    # Pedir un idioma que el video no tiene dispara NoTranscriptFound crudo
    # (el video tiene transcripciones, pero ninguna en el idioma pedido).
    fake_transcript_list(find_error=_no_transcript_found(["fr"]))

    with pytest.raises(TranscriptNotFoundError):
        get_transcript(VIDEO_ID, lang="fr")


def test_get_transcript_error_de_fetch_lanza_transcript_not_found(
    fake_transcript_list,
):
    # El bloque try también cubre la descarga: un error crudo en fetch()
    # debe mapearse igual que en el listado.
    fake_transcript_list(fetch_error=VideoUnavailable(VIDEO_ID))

    with pytest.raises(TranscriptNotFoundError):
        get_transcript(VIDEO_ID)


# ---------------------------------------------------------------------------
# Casos de error: transcripciones deshabilitadas + catch-order
# ---------------------------------------------------------------------------


def test_get_transcript_transcripciones_deshabilitadas_lanza_transcripts_disabled(
    fake_transcript_list,
):
    fake_transcript_list(find_error=TranscriptsDisabled(VIDEO_ID))

    with pytest.raises(TranscriptsDisabledError) as exc:
        get_transcript(VIDEO_ID)

    assert VIDEO_ID in str(exc.value)
    assert isinstance(exc.value.__cause__, TranscriptsDisabled)


def test_get_transcript_transcripts_disabled_no_se_malinterpreta_como_not_found(
    fake_transcript_list,
):
    # Trampa real: TranscriptsDisabled es subclase de CouldNotRetrieveTranscript.
    # El catch-order del módulo (except _YTTranscriptsDisabled primero) debe
    # traducir a TranscriptsDisabledError, NUNCA a TranscriptNotFoundError.
    fake_transcript_list(find_error=TranscriptsDisabled(VIDEO_ID))

    with pytest.raises(TranscriptsDisabledError) as exc:
        get_transcript(VIDEO_ID)

    assert not isinstance(exc.value, TranscriptNotFoundError)


def test_get_transcript_fetch_transcripciones_deshabilitadas_lanza_disabled(
    fake_transcript_list,
):
    # El mismo catch-order debe aplicarse cuando el error crudo sale de fetch().
    fake_transcript_list(fetch_error=TranscriptsDisabled(VIDEO_ID))

    with pytest.raises(TranscriptsDisabledError):
        get_transcript(VIDEO_ID)


# ---------------------------------------------------------------------------
# Casos de error: resto de la familia CouldNotRetrieveTranscript / API
# ---------------------------------------------------------------------------


def test_get_transcript_could_not_retrieve_base_lanza_transcript_not_found(
    fake_transcript_list,
):
    fake_transcript_list(find_error=CouldNotRetrieveTranscript(VIDEO_ID))

    with pytest.raises(TranscriptNotFoundError):
        get_transcript(VIDEO_ID)


def test_get_transcript_youtube_request_failed_lanza_transcript_not_found(
    fake_transcript_list,
):
    fake_transcript_list(
        find_error=YouTubeRequestFailed(VIDEO_ID, HTTPError("boom"))
    )

    with pytest.raises(TranscriptNotFoundError):
        get_transcript(VIDEO_ID)


def test_get_transcript_excepcion_generica_del_api_lanza_transcript_not_found(
    fake_transcript_list,
):
    fake_transcript_list(find_error=YouTubeTranscriptApiException())

    with pytest.raises(TranscriptNotFoundError):
        get_transcript(VIDEO_ID)


# ---------------------------------------------------------------------------
# Jerarquía de excepciones propias
# ---------------------------------------------------------------------------


def test_excepciones_propias_heredan_de_transcript_error():
    assert issubclass(TranscriptNotFoundError, TranscriptError)
    assert issubclass(TranscriptsDisabledError, TranscriptError)


def test_precondicion_transcripts_disabled_es_subclase_de_could_not_retrieve():
    # Documenta por qué los tests de catch-order mockean cada excepción cruda
    # por separado; si la librería cambia la jerarquía, este test avisa.
    assert issubclass(TranscriptsDisabled, CouldNotRetrieveTranscript)


# ---------------------------------------------------------------------------
# Helpers internos: compatibilidad v0.x / v1.x de la librería
# ---------------------------------------------------------------------------


def test_list_transcripts_usa_list_cuando_la_api_es_v1():
    llamado = []

    class _ApiV1:
        def list(self, video_id):
            llamado.append(video_id)
            return "transcript_list"

    resultado = _list_transcripts(_ApiV1(), VIDEO_ID)

    assert llamado == [VIDEO_ID]
    assert resultado == "transcript_list"


def test_list_transcripts_usa_list_transcripts_cuando_la_api_es_v0():
    # Las versiones v0.x no tienen .list; el helper debe caer en
    # list_transcripts().
    llamado = []

    class _ApiV0:
        def list_transcripts(self, video_id):
            llamado.append(video_id)
            return "transcript_list"

    resultado = _list_transcripts(_ApiV0(), VIDEO_ID)

    assert llamado == [VIDEO_ID]
    assert resultado == "transcript_list"
