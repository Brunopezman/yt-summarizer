"""Tests de la capa de resumen (`yt_summarizer.summarizer`).

Nunca se pega a APIs reales ni se requiere que los SDKs (``openai``,
``google-genai``) estén instalados: el módulo los importa de forma
diferida, así que los tests instalan SDKs falsos en ``sys.modules`` para
verificar la traducción de excepciones crudas a la jerarquía propia
(``LLMProviderError`` / ``LLMRateLimitError``), y parchean
``_call_openai``/``_call_gemini`` (los puntos de mockeo documentados) para el
comportamiento de ``summarize()``.

Casos límite del contrato cubiertos acá:

1. Rate limit simulado (mock) -> ``LLMRateLimitError`` (nunca
   ``LLMProviderError`` genérico), para ambos proveedores — en Gemini, el 429
   llega como ``ClientError`` con ``code == 429``.
2. Proveedor mal configurado (falta API key) -> ``LLMProviderError`` con el
   nombre exacto de la variable faltante.
3. Texto vacío -> ``LLMProviderError`` (decisión de integración, fail-fast).
4. Timeout de red -> ``LLMProviderError`` (no confundir con rate limit).
"""

import importlib
import sys
import types

import pytest

import yt_summarizer.summarizer as summarizer_module
from yt_summarizer.summarizer import (
    LENGTHS,
    GeminiSummarizer,
    LLMProviderError,
    LLMRateLimitError,
    OpenAISummarizer,
    Summarizer,
    _build_prompt,
    _call_gemini,
    _call_openai,
    get_summarizer,
)

# ---------------------------------------------------------------------------
# SDKs falsos: jerarquías que imitan a openai / google.genai / httpx
# ---------------------------------------------------------------------------


class _FakeOpenAIError(Exception):
    """Base de los errores del SDK de openai (como `openai.OpenAIError`)."""


class _FakeRateLimitError(_FakeOpenAIError):
    """Como `openai.RateLimitError` (429)."""


class _FakeAPITimeoutError(_FakeOpenAIError):
    """Como `openai.APITimeoutError`."""


class _FakeGenAIError(Exception):
    """Base de los errores de `google.genai.errors` (como `APIError`)."""

    def __init__(self, code, message):
        super().__init__(f"{code} {message}".strip())
        self.code = code


class _FakeClientError(_FakeGenAIError):
    """Como `google.genai.errors.ClientError` (errores 4xx)."""


class _FakeServerError(_FakeGenAIError):
    """Como `google.genai.errors.ServerError` (errores 5xx)."""


class _FakeHTTPError(Exception):
    """Base de los errores de transporte de httpx (como `httpx.HTTPError`)."""


class _FakeTimeoutError(_FakeHTTPError):
    """Como `httpx.TimeoutException` (timeout de red)."""


class _FakeGenAIResponse:
    """Simula la respuesta de `client.models.generate_content`."""

    def __init__(self, text, raise_on_text=False):
        self._text = text
        self._raise_on_text = raise_on_text

    @property
    def text(self):
        if self._raise_on_text:
            raise ValueError("bloqueado por safety settings")
        return self._text


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _module(name):
    return types.ModuleType(name)


@pytest.fixture
def fake_openai_sdk(monkeypatch):
    """Instala un SDK de openai falso en ``sys.modules``.

    Devuelve un objeto de estado configurable desde el test:

    - ``state.error``: excepción cruda a lanzar en ``chat.completions.create``.
    - ``state.response_text``: contenido a devolver (``None`` o ``""`` simulan
      respuesta vacía).
    - ``state.create_calls``: kwargs de cada llamada al SDK (modelo, prompt...).
    """

    class State:
        def __init__(self):
            self.api_key = None
            self.timeout = None
            self.create_calls = []
            self.error = None
            self.response_text = None

    state = State()

    class FakeCompletions:
        def create(self, **kwargs):
            state.create_calls.append(kwargs)
            if state.error is not None:
                raise state.error
            return _FakeResponse(state.response_text)

    class FakeChat:
        @property
        def completions(self):
            return FakeCompletions()

    class FakeOpenAIClient:
        def __init__(self, api_key=None, timeout=None):
            state.api_key = api_key
            state.timeout = timeout
            self.chat = FakeChat()

    fake_openai = _module("openai")
    fake_openai.OpenAIError = _FakeOpenAIError
    fake_openai.RateLimitError = _FakeRateLimitError
    fake_openai.APITimeoutError = _FakeAPITimeoutError
    fake_openai.OpenAI = FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    return state


@pytest.fixture
def fake_gemini_sdk(monkeypatch):
    """Instala SDKs de Google falsos (``google.genai``, ``google.genai.errors``
    y ``httpx``) en ``sys.modules``.

    Devuelve un objeto de estado configurable desde el test:

    - ``state.error``: excepción cruda a lanzar en
      ``client.models.generate_content``.
    - ``state.response_text``: texto a devolver (``None``/``""`` = vacío).
    - ``state.raise_on_text``: si ``True``, acceder a ``.text`` lanza
      ``ValueError`` (respuesta bloqueada por safety settings).
    - ``state.prompts`` / ``state.models`` / ``state.configured_keys``:
      registros de las llamadas al SDK.
    """

    class State:
        def __init__(self):
            self.prompts = []
            self.models = []
            self.configured_keys = []
            self.error = None
            self.response_text = None
            self.raise_on_text = False

    state = State()

    class FakeModels:
        def generate_content(self, *, model, contents, **kwargs):
            state.prompts.append(contents)
            state.models.append(model)
            if state.error is not None:
                raise state.error
            return _FakeGenAIResponse(state.response_text, state.raise_on_text)

    class FakeGenAIClient:
        def __init__(self, *, api_key=None, **kwargs):
            state.configured_keys.append(api_key)
            self.models = FakeModels()

    genai = _module("google.genai")
    genai.Client = FakeGenAIClient

    errors = _module("google.genai.errors")
    errors.APIError = _FakeGenAIError
    errors.ClientError = _FakeClientError
    errors.ServerError = _FakeServerError
    genai.errors = errors

    httpx = _module("httpx")
    httpx.TimeoutException = _FakeTimeoutError
    httpx.HTTPError = _FakeHTTPError

    google_pkg = _module("google")
    google_pkg.genai = genai

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    return state


@pytest.fixture
def recorder_openai(monkeypatch):
    """Parchea ``_call_openai`` registrando el prompt y devolviendo texto fijo.

    Devuelve la lista de prompts llamados para poder inspeccionarlos.
    """

    prompts = []

    def _fake_call(prompt):
        prompts.append(prompt)
        return "Resumen simulado"

    monkeypatch.setattr(summarizer_module, "_call_openai", _fake_call)
    return prompts


@pytest.fixture
def recorder_gemini(monkeypatch):
    """Parchea ``_call_gemini`` registrando el prompt y devolviendo texto fijo."""

    prompts = []

    def _fake_call(prompt):
        prompts.append(prompt)
        return "Resumen simulado"

    monkeypatch.setattr(summarizer_module, "_call_gemini", _fake_call)
    return prompts


# ---------------------------------------------------------------------------
# Jerarquía de excepciones y contrato del módulo
# ---------------------------------------------------------------------------


def test_modulo_se_importa_sin_sdks_instalados():
    # El import tolerante es parte del contrato: el módulo importa aunque
    # openai/google-genai no estén instalados (import diferido del SDK).
    modulo = importlib.import_module("yt_summarizer.summarizer")
    assert hasattr(modulo, "get_summarizer")


def test_llm_rate_limit_error_es_subclase_de_llm_provider_error():
    assert issubclass(LLMRateLimitError, LLMProviderError)


def test_lengths_expone_las_tres_longitudes_soportadas():
    assert LENGTHS == ("corto", "medio", "extenso")


# ---------------------------------------------------------------------------
# Factory: get_summarizer
# ---------------------------------------------------------------------------


def test_get_summarizer_default_es_openai_sin_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    summarizer = get_summarizer()

    assert isinstance(summarizer, OpenAISummarizer)


def test_get_summarizer_respeta_llm_provider_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    summarizer = get_summarizer()

    assert isinstance(summarizer, GeminiSummarizer)


def test_get_summarizer_argumento_explicito_prevalece_sobre_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    summarizer = get_summarizer("openai")

    assert isinstance(summarizer, OpenAISummarizer)


def test_get_summarizer_normaliza_mayusculas_y_espacios():
    summarizer = get_summarizer("  GEMINI  ")

    assert isinstance(summarizer, GeminiSummarizer)


def test_get_summarizer_proveedor_invalido_lanza_llm_provider_error():
    with pytest.raises(LLMProviderError) as exc:
        get_summarizer("claude")

    mensaje = str(exc.value)
    assert "claude" in mensaje
    assert "'openai'" in mensaje
    assert "'gemini'" in mensaje


def test_get_summarizer_env_invalida_lanza_llm_provider_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")

    with pytest.raises(LLMProviderError) as exc:
        get_summarizer()

    assert "claude" in str(exc.value)


def test_get_summarizer_no_valida_la_api_key():
    # La key se lee recién en la primera llamada a summarize().
    summarizer = get_summarizer("openai")

    assert isinstance(summarizer, Summarizer)


# ---------------------------------------------------------------------------
# Happy path: summarize() devuelve el texto del adaptador
# ---------------------------------------------------------------------------


def test_summarize_openai_devuelve_el_texto_del_adaptador(recorder_openai):
    resumen = OpenAISummarizer().summarize("Transcripción de prueba")

    assert resumen == "Resumen simulado"
    assert len(recorder_openai) == 1


def test_summarize_gemini_devuelve_el_texto_del_adaptador(recorder_gemini):
    resumen = GeminiSummarizer().summarize("Transcripción de prueba")

    assert resumen == "Resumen simulado"
    assert len(recorder_gemini) == 1


def test_summarize_usa_medio_por_defecto(recorder_openai):
    OpenAISummarizer().summarize("Transcripción")

    assert "150 y 350 palabras" in recorder_openai[0]


def test_summarize_length_influye_en_el_prompt(recorder_openai):
    summarizer = OpenAISummarizer()
    summarizer.summarize("Mismo texto", length="corto")
    summarizer.summarize("Mismo texto", length="extenso")

    assert len(recorder_openai) == 2
    assert recorder_openai[0] != recorder_openai[1]
    assert "150 palabras" in recorder_openai[0]
    assert "700 palabras" in recorder_openai[1]


def test_summarize_gemini_length_influye_en_el_prompt(recorder_gemini):
    summarizer = GeminiSummarizer()
    summarizer.summarize("Mismo texto", length="corto")
    summarizer.summarize("Mismo texto", length="extenso")

    assert len(recorder_gemini) == 2
    assert recorder_gemini[0] != recorder_gemini[1]
    assert "150 palabras" in recorder_gemini[0]
    assert "700 palabras" in recorder_gemini[1]


def test_build_prompt_incluye_transcripcion_y_instruccion_de_longitud():
    corto = _build_prompt("Hola mundo", "corto")
    extenso = _build_prompt("Hola mundo", "extenso")

    assert "Hola mundo" in corto
    assert "150 palabras" in corto
    assert "700 palabras" in extenso
    assert corto != extenso


# ---------------------------------------------------------------------------
# Casos de error de summarize(): texto vacío y longitud inválida
# ---------------------------------------------------------------------------


def test_summarize_openai_texto_vacio_lanza_llm_provider_error(monkeypatch):
    no_llamado = []

    def _fake_call(prompt):
        no_llamado.append(prompt)
        return "ok"

    monkeypatch.setattr(summarizer_module, "_call_openai", _fake_call)

    with pytest.raises(LLMProviderError) as exc:
        OpenAISummarizer().summarize("")

    assert "vacío" in str(exc.value).lower()
    assert no_llamado == []  # fail-fast: no se toca al proveedor


def test_summarize_gemini_texto_vacio_lanza_llm_provider_error(monkeypatch):
    monkeypatch.setattr(
        summarizer_module,
        "_call_gemini",
        lambda prompt: (_ for _ in ()).throw(
            AssertionError("no se debe llamar al proveedor")
        ),
    )

    with pytest.raises(LLMProviderError) as exc:
        GeminiSummarizer().summarize("   ")

    assert "vacío" in str(exc.value).lower()


def test_summarize_texto_solo_espacios_lanza_llm_provider_error(recorder_openai):
    with pytest.raises(LLMProviderError):
        OpenAISummarizer().summarize("   \n\t  ")

    assert recorder_openai == []


@pytest.mark.parametrize("length", ["", "ultra", "extensísimo", None])
def test_summarize_length_invalido_lanza_llm_provider_error(recorder_openai, length):
    with pytest.raises(LLMProviderError) as exc:
        OpenAISummarizer().summarize("Transcripción", length=length)

    mensaje = str(exc.value)
    assert "'corto'" in mensaje
    assert "'medio'" in mensaje
    assert "'extenso'" in mensaje
    assert recorder_openai == []  # fail-fast: no se toca al proveedor


# ---------------------------------------------------------------------------
# Rate limit (caso límite 1 del contrato): nunca degradar a LLMProviderError
# ---------------------------------------------------------------------------


def test_call_openai_rate_limit_lanza_llm_rate_limit_error(
    monkeypatch, fake_openai_sdk
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_openai_sdk.error = _FakeRateLimitError("429 too many requests")

    with pytest.raises(LLMRateLimitError) as exc:
        _call_openai("prompt")

    assert type(exc.value) is LLMRateLimitError
    assert isinstance(exc.value, LLMProviderError)  # jerarquía respetada


def test_call_gemini_client_error_429_lanza_llm_rate_limit_error(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.error = _FakeClientError(429, "quota agotada")

    with pytest.raises(LLMRateLimitError) as exc:
        _call_gemini("prompt")

    assert type(exc.value) is LLMRateLimitError


def test_call_gemini_client_error_no_429_lanza_llm_provider_error(
    monkeypatch, fake_gemini_sdk
):
    # Errores 4xx que no son 429 (API key inválida, modelo inexistente, etc.)
    # NO se degradan a rate limit: son errores del proveedor.
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.error = _FakeClientError(403, "forbidden")

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert not isinstance(exc.value, LLMRateLimitError)
    assert "proveedor Gemini" in str(exc.value)


def test_summarize_openai_propaga_llm_rate_limit_error_sin_degradar(monkeypatch):
    # El rate limit llega ya traducido desde _call_openai; summarize() debe
    # propagarlo como LLMRateLimitError y nunca envolverlo en LLMProviderError.
    def _fake_call(prompt):
        raise LLMRateLimitError("429 rate limited")

    monkeypatch.setattr(summarizer_module, "_call_openai", _fake_call)

    with pytest.raises(LLMRateLimitError) as exc:
        OpenAISummarizer().summarize("Transcripción")

    assert type(exc.value) is LLMRateLimitError


# ---------------------------------------------------------------------------
# Timeout de red (caso límite 4 del contrato): LLMProviderError, no rate limit
# ---------------------------------------------------------------------------


def test_call_openai_timeout_lanza_llm_provider_error_no_rate_limit(
    monkeypatch, fake_openai_sdk
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_openai_sdk.error = _FakeAPITimeoutError("request timed out")

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert not isinstance(exc.value, LLMRateLimitError)
    assert "timeout" in str(exc.value).lower()


def test_call_gemini_timeout_lanza_llm_provider_error_no_rate_limit(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.error = _FakeTimeoutError("request timed out")

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert not isinstance(exc.value, LLMRateLimitError)
    assert "timeout" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Proveedor mal configurado (caso límite 2 del contrato): falta API key
# ---------------------------------------------------------------------------


def test_call_openai_key_faltante_lanza_llm_provider_error_con_nombre_variable(
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert "OPENAI_API_KEY" in str(exc.value)


def test_call_gemini_key_faltante_lanza_llm_provider_error_con_nombre_variable(
    monkeypatch,
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert "GEMINI_API_KEY" in str(exc.value)


def test_call_openai_key_faltante_prevalece_sobre_sdk_ausente(monkeypatch):
    # La key se lee ANTES del import diferido del SDK: si falta la key, se
    # reporta la key aunque el SDK tampoco esté (o esté roto).
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "openai", None)

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert "OPENAI_API_KEY" in str(exc.value)
    assert "no está instalado" not in str(exc.value)


# ---------------------------------------------------------------------------
# SDK no instalado
# ---------------------------------------------------------------------------


def test_call_openai_sdk_no_instalado_lanza_llm_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # sys.modules con None hace que `import openai` falle con ImportError.
    monkeypatch.setitem(sys.modules, "openai", None)

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert "SDK de OpenAI no está instalado" in str(exc.value)


def test_call_gemini_sdk_no_instalado_lanza_llm_provider_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setitem(sys.modules, "google", None)

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert "SDK de Google Gemini no está instalado" in str(exc.value)


# ---------------------------------------------------------------------------
# Errores genéricos del proveedor y respuestas vacías
# ---------------------------------------------------------------------------


def test_call_openai_error_generico_lanza_llm_provider_error(
    monkeypatch, fake_openai_sdk
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_openai_sdk.error = _FakeOpenAIError("500 internal server error")

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert not isinstance(exc.value, LLMRateLimitError)
    assert "proveedor OpenAI" in str(exc.value)


def test_call_openai_respuesta_vacia_lanza_llm_provider_error(
    monkeypatch, fake_openai_sdk
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_openai_sdk.response_text = ""

    with pytest.raises(LLMProviderError) as exc:
        _call_openai("prompt")

    assert "vacía" in str(exc.value).lower()


def test_call_openai_respuesta_solo_espacios_lanza_llm_provider_error(
    monkeypatch, fake_openai_sdk
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_openai_sdk.response_text = "   \n "

    with pytest.raises(LLMProviderError):
        _call_openai("prompt")


def test_call_gemini_respuesta_vacia_lanza_llm_provider_error(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.response_text = ""

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert "vacía" in str(exc.value).lower()


def test_call_gemini_respuesta_bloqueada_lanza_llm_provider_error(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.raise_on_text = True

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert "no devolvió contenido" in str(exc.value)


def test_call_gemini_error_generico_lanza_llm_provider_error(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    fake_gemini_sdk.error = _FakeServerError(500, "interno")

    with pytest.raises(LLMProviderError) as exc:
        _call_gemini("prompt")

    assert not isinstance(exc.value, LLMRateLimitError)


# ---------------------------------------------------------------------------
# Integridad de la llamada al SDK (lo que llega al proveedor)
# ---------------------------------------------------------------------------


def test_call_openai_envia_prompt_y_modelo_al_sdk(monkeypatch, fake_openai_sdk):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-custom")
    fake_openai_sdk.response_text = "Resumen"

    resultado = _call_openai("prompt de prueba")

    assert resultado == "Resumen"
    assert fake_openai_sdk.api_key == "sk-test"
    assert len(fake_openai_sdk.create_calls) == 1
    llamada = fake_openai_sdk.create_calls[0]
    assert llamada["model"] == "gpt-4o-custom"
    assert llamada["messages"] == [{"role": "user", "content": "prompt de prueba"}]


def test_call_openai_usa_modelo_por_defecto_sin_env(monkeypatch, fake_openai_sdk):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    fake_openai_sdk.response_text = "Resumen"

    _call_openai("prompt")

    assert fake_openai_sdk.create_calls[0]["model"] == "gpt-4o-mini"


def test_call_gemini_envia_prompt_y_usa_modelo_por_defecto(
    monkeypatch, fake_gemini_sdk
):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    fake_gemini_sdk.response_text = "Resumen gemini"

    resultado = _call_gemini("prompt de prueba")

    assert resultado == "Resumen gemini"
    assert fake_gemini_sdk.configured_keys == ["gem-test"]
    assert fake_gemini_sdk.models == ["gemini-2.0-flash"]
    assert fake_gemini_sdk.prompts == ["prompt de prueba"]
