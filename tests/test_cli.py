"""Tests de la capa CLI (`yt_summarizer.cli`).

Se testea la capa de interacción con `typer.testing.CliRunner` (forma canónica
de probar apps Typer sin subprocesos ni red): nunca se pega a YouTube ni a
APIs de LLM. La capa de negocio (`get_transcript`, `get_summarizer`) se
mockea/parchea sobre `yt_summarizer.cli` para simular cada escenario.

Convenciones del contrato verificadas:

- el resumen es la única salida de negocio → **stdout**;
- progreso, confirmaciones y errores → **stderr**;
- errores de uso (URL o flag inválido) → exit code 2;
- errores de negocio/configuración (transcripción, LLM, API keys) → exit code
  1, siempre con mensaje claro en español y nunca un traceback crudo;
- fail-fast de configuración: la API key se valida ANTES de tocar red.

El spinner/colores de rich se apagan (``_console = None``) para que los
mensajes de stderr sean texto plano y los asserts deterministas.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import yt_summarizer.cli as cli_module
from yt_summarizer.cli import InvalidVideoURLError, app, extract_video_id
from yt_summarizer.summarizer import LLMProviderError, LLMRateLimitError
from yt_summarizer.transcript import TranscriptNotFoundError, TranscriptsDisabledError

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"

#: Entorno con las keys ya cargadas, para llegar al pipeline de negocio.
ENV_OPENAI_OK = {"OPENAI_API_KEY": "sk-test", "LLM_PROVIDER": "openai"}
ENV_GEMINI_OK = {"GEMINI_API_KEY": "gem-test", "LLM_PROVIDER": "gemini"}


class _FakeSummarizer:
    """Simula un `Summarizer`: registra argumentos y devuelve/levanta según
    configuración. Imita el fail-fast de `summarize()` con texto vacío."""

    def __init__(self, texto="Resumen simulado", error=None):
        self.texto = texto
        self.error = error
        self.llamadas = []

    def summarize(self, text, length="medio"):
        self.llamadas.append({"text": text, "length": length})
        if self.error is not None:
            raise self.error
        if not text or not text.strip():
            raise LLMProviderError(
                "El texto a resumir está vacío. No se puede generar un resumen "
                "sin transcripción."
            )
        return self.texto


@pytest.fixture
def cli_basica(monkeypatch):
    """Runner con entorno de configuración limpio y stderr plano (sin rich).

    - Borra `OPENAI_API_KEY`/`GEMINI_API_KEY`/`LLM_PROVIDER` del entorno real
      para que los tests no dependan de lo que tenga exportado el runner.
    - Apaga el console de rich para que stderr sea texto plano y capturable.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(cli_module, "_console", None)
    return CliRunner()


# ---------------------------------------------------------------------------
# extract_video_id: unit tests del parseo de URL → video_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",  # sin esquema
    ],
)
def test_extract_video_id_urls_watch_devuelven_id(url):
    assert extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=30#fragmento",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_youtu_be_shorts_embed_live_devuelven_id(url):
    assert extract_video_id(url) == VIDEO_ID


def test_extract_video_id_id_crudo_devuelve_el_mismo_id():
    assert extract_video_id(VIDEO_ID) == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        # Dominio falso: termina en .youtube.com pero no es de YouTube.
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.co/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_dominio_falso_lanza_invalid_video_url(url):
    with pytest.raises(InvalidVideoURLError):
        extract_video_id(url)


@pytest.mark.parametrize(
    "url",
    [
        # Host válido pero sin ID extraíble.
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=",
        "https://www.youtube.com/",
        # ID con longitud incorrecta.
        "https://www.youtube.com/watch?v=abc",
        # Texto que no es URL ni ID.
        "no-soy-una-url",
    ],
)
def test_extract_video_id_sin_id_extraible_lanza_invalid_video_url(url):
    with pytest.raises(InvalidVideoURLError):
        extract_video_id(url)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_resumir_exitoso_imprime_resumen_en_stdout(cli_basica, monkeypatch):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción de prueba."
    )
    fake = _FakeSummarizer("Resumen simulado")
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 0
    assert result.exception is None
    # stdout = SOLO el resumen (pipeable): nada de progreso ni confirmaciones.
    assert result.stdout.strip() == "Resumen simulado"
    assert "Resumen simulado" not in result.stderr
    # La confirmación de guardado va a stderr.
    assert "Usá --output" in result.stderr


def test_resumir_con_output_escribe_archivo_y_mantiene_stdout(
    cli_basica, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción de prueba."
    )
    fake = _FakeSummarizer("Resumen simulado")
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)
    destino = tmp_path / "resumen.txt"

    result = cli_basica.invoke(
        app,
        ["resumir", VIDEO_URL, "--output", str(destino)],
        env=ENV_OPENAI_OK,
    )

    assert result.exit_code == 0
    # El resumen sigue siendo la única salida de negocio en stdout.
    assert result.stdout.strip() == "Resumen simulado"
    assert "Resumen guardado en" in result.stderr
    # El archivo se escribe con el resumen.
    assert destino.read_text(encoding="utf-8") == "Resumen simulado\n"


def test_resumir_propaga_idioma_a_get_transcript(cli_basica, monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        cli_module,
        "get_transcript",
        lambda video_id, lang="es": llamadas.append({"video_id": video_id, "lang": lang})
        or "Transcripción.",
    )
    fake = _FakeSummarizer()
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL, "--idioma", "fr"], env=ENV_OPENAI_OK
    )

    assert result.exit_code == 0
    assert llamadas == [{"video_id": VIDEO_ID, "lang": "fr"}]


def test_resumir_propaga_largo_a_summarize(cli_basica, monkeypatch):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción."
    )
    fake = _FakeSummarizer()
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL, "--largo", "extenso"], env=ENV_OPENAI_OK
    )

    assert result.exit_code == 0
    assert fake.llamadas[-1]["length"] == "extenso"


def test_resumir_proveedor_gemini_pasa_proveedor_al_summarizer(
    cli_basica, monkeypatch
):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción."
    )
    fake = _FakeSummarizer()
    provistos = []
    monkeypatch.setattr(
        cli_module,
        "get_summarizer",
        lambda provider=None: provistos.append(provider) or fake,
    )

    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL, "--proveedor", "gemini"], env=ENV_GEMINI_OK
    )

    assert result.exit_code == 0
    assert provistos == ["gemini"]


# ---------------------------------------------------------------------------
# Caso límite 1: URL inválida → exit code 2, mensaje claro, sin traceback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "no-soy-una-url",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch",
    ],
)
def test_resumir_url_invalida_exit_2_sin_traceback(cli_basica, url):
    # Para el caso 1 no hace falta key: el parseo falla antes de la config.
    result = cli_basica.invoke(app, ["resumir", url], env=ENV_OPENAI_OK)

    assert result.exit_code == 2
    assert result.stdout == ""
    # Mensaje claro que menciona la URL problemática, en stderr.
    assert url in result.stderr
    # No es un error inesperado: es el mecanismo normal de exit de Typer.
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 2


# ---------------------------------------------------------------------------
# Caso límite 2: falta API key → exit code 1, fail-fast (no descarga nada)
# ---------------------------------------------------------------------------


def test_resumir_falta_api_key_exit_1_nombra_variable_y_fail_fast(
    cli_basica, monkeypatch
):
    # Si la key faltara recién al llamar al LLM no se vería; acá se demuestra
    # que NO se llega a tocar la capa de transcripción (ni de resumen).
    llamadas_transcript = []
    llamadas_summarizer = []

    def _get_transcript(video_id, lang="es"):
        llamadas_transcript.append(video_id)
        return "Transcripción."

    def _get_summarizer(provider=None):
        llamadas_summarizer.append(provider)
        return _FakeSummarizer()

    monkeypatch.setattr(cli_module, "get_transcript", _get_transcript)
    monkeypatch.setattr(cli_module, "get_summarizer", _get_summarizer)

    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL], env={"OPENAI_API_KEY": None}
    )

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stderr
    # Fail-fast: la validación de la key ocurre antes de tocar red.
    assert llamadas_transcript == []
    assert llamadas_summarizer == []
    assert isinstance(result.exception, SystemExit)


def test_resumir_falta_api_key_gemini_nombra_la_variable_correcta(
    cli_basica, monkeypatch
):
    llamado = []
    monkeypatch.setattr(
        cli_module,
        "get_transcript",
        lambda video_id, lang="es": llamado.append(video_id) or "x",
    )

    result = cli_basica.invoke(
        app,
        ["resumir", VIDEO_URL, "--proveedor", "gemini"],
        env={"GEMINI_API_KEY": None, "OPENAI_API_KEY": None},
    )

    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.stderr
    assert llamado == []  # fail-fast también con el proveedor forzado


def test_resumir_llm_provider_env_invalido_exit_1(cli_basica):
    result = cli_basica.invoke(
        app,
        ["resumir", VIDEO_URL],
        env={"LLM_PROVIDER": "claude", "OPENAI_API_KEY": "sk-test"},
    )

    assert result.exit_code == 1
    assert "claude" in result.stderr
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# Caso límite 3: error de negocio de transcripción → exit 1, mensaje claro
# ---------------------------------------------------------------------------


def test_resumir_sin_transcripcion_exit_1_mensaje_claro(cli_basica, monkeypatch):
    def _sin_transcripcion(video_id, lang="es"):
        raise TranscriptNotFoundError(
            f"No se pudo obtener una transcripción para el video '{video_id}' "
            f"en el idioma '{lang}'."
        )

    monkeypatch.setattr(cli_module, "get_transcript", _sin_transcripcion)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "No se pudo obtener una transcripción" in result.stderr
    assert VIDEO_ID in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_resumir_transcripciones_deshabilitadas_exit_1(cli_basica, monkeypatch):
    def _deshabilitadas(video_id, lang="es"):
        raise TranscriptsDisabledError(
            f"Las transcripciones del video '{video_id}' están deshabilitadas "
            "por el canal."
        )

    monkeypatch.setattr(cli_module, "get_transcript", _deshabilitadas)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert "deshabilitadas" in result.stderr
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# Caso límite 4: error de negocio del LLM → exit 1, sin traceback
# ---------------------------------------------------------------------------


def test_resumir_rate_limit_llm_exit_1_mensaje_distinto(cli_basica, monkeypatch):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción."
    )
    fake = _FakeSummarizer(error=LLMRateLimitError("Rate limit de la API de OpenAI: 429"))
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert "Rate limit" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_resumir_error_generico_llm_exit_1(cli_basica, monkeypatch):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción."
    )
    fake = _FakeSummarizer(error=LLMProviderError("Error del proveedor OpenAI: 500"))
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert "Error del proveedor OpenAI" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_resumir_transcripcion_vacia_error_llm_exit_1(cli_basica, monkeypatch):
    # Transcripción vacía: la capa de resumen falla con LLMProviderError
    # (fail-fast del negocio) y la CLI lo traduce a exit 1 sin traceback.
    monkeypatch.setattr(cli_module, "get_transcript", lambda video_id, lang="es": "")

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert "vacío" in result.stderr.lower()
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# Caso límite 5: excepción inesperada de la capa de negocio → exit 1, mensaje
# claro en español a stderr, sin traceback, stdout vacío (obs-001)
# ---------------------------------------------------------------------------


def test_resumir_excepcion_inesperada_negocio_exit_1_sin_traceback(
    cli_basica, monkeypatch
):
    # Una excepción fuera del contrato (p. ej. ValueError) que escape de
    # get_transcript no debe llegar como traceback crudo: la red de seguridad
    # del CLI la traduce a un mensaje claro en español con exit code 1.
    def _valor_error(video_id, lang="es"):
        raise ValueError("id malformado internamente: 1234")

    monkeypatch.setattr(cli_module, "get_transcript", _valor_error)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error inesperado al procesar el video" in result.stderr
    assert VIDEO_ID in result.stderr
    # El detalle de la excepción se incluye breve en el mensaje para debuggear.
    assert "id malformado internamente: 1234" in result.stderr
    # Sin traceback crudo: solo el mensaje claro y el exit normal de Typer.
    assert "Traceback" not in result.stderr
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 1


@pytest.mark.parametrize(
    ("excepcion", "codigo_esperado"),
    [
        # SystemExit: mecanismo normal de salida de Typer, no se re-traduce.
        (SystemExit(3), 3),
        # KeyboardInterrupt: Ctrl+C, se propaga (exit 130 por convención).
        (KeyboardInterrupt(), 130),
    ],
)
def test_resumir_base_exception_negocio_no_es_tragada_por_red_de_seguridad(
    cli_basica, monkeypatch, excepcion, codigo_esperado
):
    # La red de seguridad es `except Exception`: SystemExit/KeyboardInterrupt
    # heredan de BaseException y deben propagar sin ser traducidos a
    # "Error inesperado" (declarado en obs-001).
    def _lanza(video_id, lang="es"):
        raise excepcion

    monkeypatch.setattr(cli_module, "get_transcript", _lanza)

    result = cli_basica.invoke(app, ["resumir", VIDEO_URL], env=ENV_OPENAI_OK)

    assert result.exit_code == codigo_esperado
    assert "Error inesperado al procesar el video" not in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# Errores de escritura de --output → exit 1, sin traceback
# ---------------------------------------------------------------------------


def test_resumir_output_no_escribible_exit_1(cli_basica, monkeypatch):
    monkeypatch.setattr(
        cli_module, "get_transcript", lambda video_id, lang="es": "Transcripción."
    )
    fake = _FakeSummarizer()
    monkeypatch.setattr(cli_module, "get_summarizer", lambda provider=None: fake)

    result = cli_basica.invoke(
        app,
        ["resumir", VIDEO_URL, "--output", "/ruta/que/no/existe/out.txt"],
        env=ENV_OPENAI_OK,
    )

    assert result.exit_code == 1
    assert "No se pudo escribir el resumen" in result.stderr
    assert isinstance(result.exception, SystemExit)
    # obs-002: el resumen se persiste ANTES de imprimirse a stdout, así un
    # fallo de escritura no deja salida de negocio consumida por el pipe.
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# Errores de uso (flags inválidos) → exit code 2, manejados por Typer
# ---------------------------------------------------------------------------


def test_resumir_flag_largo_invalido_exit_2(cli_basica):
    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL, "--largo", "ultra"], env=ENV_OPENAI_OK
    )

    assert result.exit_code == 2
    assert "ultra" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_resumir_flag_proveedor_invalido_exit_2(cli_basica):
    result = cli_basica.invoke(
        app, ["resumir", VIDEO_URL, "--proveedor", "claude"], env=ENV_OPENAI_OK
    )

    assert result.exit_code == 2
    assert "claude" in result.stderr
    assert isinstance(result.exception, SystemExit)
