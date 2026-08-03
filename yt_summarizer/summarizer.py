"""Generación de resúmenes vía LLM (OpenAI / Gemini).

Capa de IA del proyecto: expone la interfaz común :class:`Summarizer` y sus
implementaciones concretas por proveedor (:class:`OpenAISummarizer` y
:class:`GeminiSummarizer`), seleccionables por la variable de entorno
``LLM_PROVIDER`` (valores válidos: ``openai``, ``gemini``; default ``openai``).

Los errores propios de esta capa se agrupan bajo :class:`LLMProviderError`:

- :class:`LLMRateLimitError` — el proveedor respondió con rate limit (429 en
  OpenAI, ``ResourceExhausted`` en Gemini). Es subclase de
  :class:`LLMProviderError`, así que el llamador puede capturar todos los
  fallos de la capa con un solo ``except LLMProviderError``.

Las excepciones crudas de los SDKs (``openai``, ``google-genai``)
nunca se propagan al llamador: se traducen dentro de ``_call_openai()`` y
``_call_gemini()`` a estas excepciones propias y tipadas.

Las API keys se leen de las variables de entorno ``OPENAI_API_KEY`` y
``GEMINI_API_KEY`` en el momento de la llamada; nunca se hardcodean ni se
loguean. Los SDKs se importan de forma diferida, así que importar este módulo
funciona aunque no estén instalados (si faltan, se obtiene
:class:`LLMProviderError` al intentar usarlos).

Ejemplo de uso::

    from yt_summarizer.summarizer import get_summarizer

    summarizer = get_summarizer()  # respeta LLM_PROVIDER; default "openai"
    resumen = summarizer.summarize(texto, length="medio")

Para testear sin pegar a APIs reales: el único punto que toca los SDKs es la
función de módulo ``_call_openai(prompt)`` (resp. ``_call_gemini(prompt)``).
Parcheando esa función (p. ej. con ``monkeypatch`` de pytest) se puede
simular la respuesta del proveedor o cualquiera de sus errores sin red.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Final

__all__ = [
    "Summarizer",
    "OpenAISummarizer",
    "GeminiSummarizer",
    "get_summarizer",
    "LENGTHS",
    "LLMProviderError",
    "LLMRateLimitError",
]

# Tiempo máximo de espera por cada llamada al proveedor (segundos).
LLM_TIMEOUT_SECONDS: Final[float] = 60.0

#: Longitudes de resumen soportadas por el parámetro ``length``.
LENGTHS: Final[tuple[str, ...]] = ("corto", "medio", "extenso")

#: Instrucción de formato/longitud que se inyecta en el prompt por nivel.
_LENGTH_SPECS: Final[dict[str, str]] = {
    "corto": (
        "Resumí en un texto breve de hasta 150 palabras: un párrafo con la "
        "idea central seguido de una lista de 3 a 5 viñetas con los puntos "
        "clave."
    ),
    "medio": (
        "Resumí en un texto de entre 150 y 350 palabras: un párrafo de "
        "contexto, una lista de viñetas con los puntos clave y una frase "
        "final con la conclusión."
    ),
    "extenso": (
        "Resumí en un texto extenso de hasta 700 palabras organizado en "
        "secciones con encabezados (Introducción, Puntos clave, Conclusión), "
        "desarrollando todos los temas y matices de la transcripción."
    ),
}

#: Registro de implementaciones disponibles. Punto de extensión para agregar
#: nuevos proveedores sin tocar la firma pública de ``summarize()``.
_PROVIDER_REGISTRY: Final[dict[str, type["Summarizer"]]] = {}


def _register(provider: type["Summarizer"]) -> type["Summarizer"]:
    """Registra una implementación en el registro de proveedores."""
    _PROVIDER_REGISTRY[provider.provider_name] = provider
    return provider


class LLMProviderError(Exception):
    """Error de la capa de IA (configuración, red o respuesta del proveedor).

    Es la clase base de la jerarquía: captura cualquier fallo de la capa de
    resumen con un solo ``except LLMProviderError``. Cubre:

    - proveedor mal configurado (falta la API key, SDK no instalado,
      ``LLM_PROVIDER`` inválido);
    - fallo de red o timeout al consultar al proveedor;
    - respuesta vacía o ilegible por parte del proveedor.
    """


class LLMRateLimitError(LLMProviderError):
    """El proveedor LLM respondió con rate limit (429 / ResourceExhausted).

    Se lanza cuando OpenAI devuelve ``RateLimitError`` (status 429) o cuando
    Gemini devuelve ``ResourceExhausted`` (incluido el caso en que la
    excepción aparece como causa de un ``RetryError``). Nunca se degrada a
    :class:`LLMProviderError` genérico.
    """


def _validate_inputs(text: str, length: str) -> None:
    """Valida los argumentos de ``summarize()`` antes de tocar el proveedor.

    Falla rápido (fail-fast) para no gastar una llamada al LLM con entradas
    inutilizables.

    Raises:
        LLMProviderError: si ``text`` está vacío (solo espacios en blanco) o
            si ``length`` no es uno de :data:`LENGTHS`.
    """
    if not text or not text.strip():
        raise LLMProviderError(
            "El texto a resumir está vacío. No se puede generar un resumen "
            "sin transcripción."
        )
    if length not in _LENGTH_SPECS:
        opciones = ", ".join(f"'{opcion}'" for opcion in LENGTHS)
        raise LLMProviderError(
            f"Longitud de resumen inválida: '{length}'. "
            f"Valores válidos: {opciones}."
        )


def _build_prompt(text: str, length: str) -> str:
    """Construye el prompt completo de resumen a partir de la transcripción.

    El texto resultante es único para ambos proveedores: el rol, las reglas y
    la instrucción de longitud (según ``length``) se inyectan en el contenido,
    así el comportamiento no depende del formato de mensajes de cada SDK.

    Args:
        text: transcripción en texto plano.
        length: nivel de detalle deseado (``corto``, ``medio`` o ``extenso``).
            Debe estar validado antes por :func:`_validate_inputs`.

    Returns:
        El prompt listo para enviar al proveedor.

    Ejemplo::

        from yt_summarizer.summarizer import _build_prompt

        prompt = _build_prompt("Hola mundo", "corto")
    """
    instruccion_longitud = _LENGTH_SPECS[length]
    return (
        "Sos un asistente experto en análisis de contenido audiovisual.\n"
        "Resumí la siguiente transcripción de un video de YouTube siguiendo "
        "estas reglas:\n"
        "- Devolvé SOLO el resumen, sin comentarios previos ni aclaraciones.\n"
        "- Escribí el resumen en el mismo idioma que la transcripción.\n"
        "- No inventes datos, nombres ni cifras que no aparezcan en el texto.\n"
        f"- {instruccion_longitud}\n"
        "\n"
        "Transcripción:\n"
        f"<transcripcion>\n{text}\n</transcripcion>"
    )


class Summarizer(ABC):
    """Interfaz común para generadores de resúmenes vía LLM.

    Cada proveedor implementa :meth:`summarize` usando la misma firma pública;
    el resto del proyecto (CLI, tests) depende solo de esta interfaz. Un nuevo
    proveedor se agrega subclasificando ``Summarizer`` y registrándolo en el
    registro (ver :func:`_register`), sin cambiar la firma de
    :meth:`summarize`.

    Attributes:
        provider_name: identificador del proveedor (``"openai"``, ``"gemini"``).
    """

    provider_name: str = ""

    @abstractmethod
    def summarize(self, text: str, length: str = "medio") -> str:
        """Devuelve un resumen en texto de la transcripción dada.

        Args:
            text: transcripción en texto plano.
            length: nivel de detalle del resumen: ``"corto"``, ``"medio"`` o
                ``"extenso"``. Influye en la instrucción de longitud del
                prompt (máximo de palabras y formato).

        Returns:
            El resumen generado por el proveedor, en texto plano.

        Raises:
            LLMProviderError: si el proveedor no está configurado (falta la
                API key o el SDK), si hay un fallo de red o timeout, si el
                texto está vacío, si ``length`` es inválido, o si el proveedor
                devuelve una respuesta vacía.
            LLMRateLimitError: si el proveedor responde con rate limit.

        Ejemplo::

            from yt_summarizer.summarizer import get_summarizer

            summarizer = get_summarizer()
            resumen = summarizer.summarize(texto, length="medio")
        """
        raise NotImplementedError


@_register
class OpenAISummarizer(Summarizer):
    """Resúmenes vía la API de OpenAI (Chat Completions).

    La API key se lee de ``OPENAI_API_KEY`` y el modelo de ``OPENAI_MODEL``
    (default ``gpt-4o-mini``) en el momento de la llamada. El timeout por
    request es de :data:`LLM_TIMEOUT_SECONDS` segundos.

    Para testear sin llamar a la API real, parcheá la función de módulo
    ``yt_summarizer.summarizer._call_openai``.
    """

    provider_name = "openai"

    def summarize(self, text: str, length: str = "medio") -> str:
        _validate_inputs(text, length)
        prompt = _build_prompt(text, length)
        return _call_openai(prompt)


@_register
class GeminiSummarizer(Summarizer):
    """Resúmenes vía la API de Google Gemini (google-genai).

    La API key se lee de ``GEMINI_API_KEY`` y el modelo de ``GEMINI_MODEL``
    (default ``gemini-2.0-flash``) en el momento de la llamada.

    Para testear sin llamar a la API real, parcheá la función de módulo
    ``yt_summarizer.summarizer._call_gemini``.
    """

    provider_name = "gemini"

    def summarize(self, text: str, length: str = "medio") -> str:
        _validate_inputs(text, length)
        prompt = _build_prompt(text, length)
        return _call_gemini(prompt)


def get_summarizer(provider: str | None = None) -> Summarizer:
    """Devuelve la implementación de :class:`Summarizer` para el proveedor.

    El proveedor se resuelve en este orden: el argumento ``provider`` (si se
    pasa), luego la variable de entorno ``LLM_PROVIDER`` y, si ninguna está
    definida, el default ``"openai"``. El valor se normaliza a minúsculas y
    sin espacios alrededor.

    Args:
        provider: nombre del proveedor (``"openai"`` o ``"gemini"``). Si es
            ``None``, se usa ``LLM_PROVIDER`` o ``"openai"``.

    Returns:
        Una instancia lista para usarse. La API key NO se valida acá: se lee
        recién en la primera llamada a :meth:`Summarizer.summarize`.

    Raises:
        LLMProviderError: si el proveedor no está registrado.

    Ejemplo::

        from yt_summarizer.summarizer import get_summarizer

        summarizer = get_summarizer("gemini")  # fuerza Gemini, ignora la env
        resumen = summarizer.summarize(texto, length="extenso")
    """
    nombre = (provider or os.environ.get("LLM_PROVIDER") or "openai").strip().lower()
    try:
        clase = _PROVIDER_REGISTRY[nombre]
    except KeyError:
        opciones = ", ".join(f"'{p}'" for p in sorted(_PROVIDER_REGISTRY))
        raise LLMProviderError(
            f"Proveedor LLM inválido: '{nombre}'. "
            f"Valores válidos: {opciones}. "
            "Configuralo con la variable de entorno LLM_PROVIDER."
        ) from None
    return clase()


def _call_openai(prompt: str) -> str:
    """Única función del módulo que toca el SDK de OpenAI.

    Es el punto de extensión para mockear en tests: parcheando esta función
    se evita por completo la red y el SDK. Acá se lee la API key, se importa
    el SDK (de forma diferida) y se traducen las excepciones crudas a la
    jerarquía propia.

    Args:
        prompt: prompt completo generado por :func:`_build_prompt`.

    Returns:
        El texto del resumen devuelto por el modelo.

    Raises:
        LLMProviderError: si falta ``OPENAI_API_KEY``, si el SDK no está
            instalado, si hay timeout/fallo de red, o si la respuesta es
            vacía.
        LLMRateLimitError: si OpenAI responde con ``RateLimitError`` (429).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMProviderError(
            "Falta la variable de entorno 'OPENAI_API_KEY'. "
            "Configurala para usar el proveedor OpenAI."
        )
    try:
        import openai
    except ImportError as exc:
        raise LLMProviderError(
            "El SDK de OpenAI no está instalado. Instalalo con "
            "'pip install openai' o elegí otro proveedor con LLM_PROVIDER."
        ) from exc

    try:
        client = openai.OpenAI(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
    except openai.RateLimitError as exc:
        # Nunca degradar el rate limit a un LLMProviderError genérico.
        raise LLMRateLimitError(f"Rate limit de la API de OpenAI: {exc}") from exc
    except openai.APITimeoutError as exc:
        raise LLMProviderError(
            f"Timeout al consultar la API de OpenAI "
            f"(máx. {LLM_TIMEOUT_SECONDS:.0f}s): {exc}"
        ) from exc
    except openai.OpenAIError as exc:
        # APIConnectionError (red), APIStatusError (4xx/5xx), etc.
        raise LLMProviderError(f"Error del proveedor OpenAI: {exc}") from exc

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise LLMProviderError(
            "OpenAI devolvió una respuesta vacía para el resumen."
        )
    return content


def _call_gemini(prompt: str) -> str:
    """Única función del módulo que toca el SDK de Google Gemini.

    Es el punto de extensión para mockear en tests: parcheando esta función
    se evita por completo la red y el SDK. Acá se lee la API key, se importa
    el SDK (de forma diferida) y se traducen las excepciones crudas a la
    jerarquía propia.

    Args:
        prompt: prompt completo generado por :func:`_build_prompt`.

    Returns:
        El texto del resumen devuelto por el modelo.

    Raises:
        LLMProviderError: si falta ``GEMINI_API_KEY``, si el SDK no está
            instalado, si hay timeout/fallo de red, o si la respuesta es
            vacía.
        LLMRateLimitError: si Gemini responde con rate limit (HTTP 429, que el
            SDK expone como ``ClientError`` con ``code == 429``).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMProviderError(
            "Falta la variable de entorno 'GEMINI_API_KEY'. "
            "Configurala para usar el proveedor Gemini."
        )
    try:
        from google import genai
        from google.genai import errors as genai_errors
        import httpx
    except ImportError as exc:
        raise LLMProviderError(
            "El SDK de Google Gemini no está instalado. Instalalo con "
            "'pip install google-genai' o elegí otro proveedor con "
            "LLM_PROVIDER."
        ) from exc

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            contents=prompt,
        )
    except genai_errors.ClientError as exc:
        # Errores 4xx. El 429 es rate limit/cuota: nunca degradar a
        # LLMProviderError. El resto (API key inválida, modelo inexistente,
        # etc.) es un error del proveedor.
        if exc.code == 429:
            raise LLMRateLimitError(f"Rate limit de la API de Gemini: {exc}") from exc
        raise LLMProviderError(f"Error del proveedor Gemini: {exc}") from exc
    except genai_errors.ServerError as exc:
        # Errores 5xx del lado de Google.
        raise LLMProviderError(f"Error del proveedor Gemini: {exc}") from exc
    except genai_errors.APIError as exc:
        # Cualquier otro error tipado por el SDK de Google.
        raise LLMProviderError(f"Error del proveedor Gemini: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise LLMProviderError(f"Timeout al consultar la API de Gemini: {exc}") from exc
    except httpx.HTTPError as exc:
        # Fallo de red/transporte (connect, dns, etc.), no de la API en sí.
        raise LLMProviderError(f"Error de red al consultar la API de Gemini: {exc}") from exc

    try:
        text = response.text
    except (ValueError, AttributeError) as exc:
        # Respuesta bloqueada por safety settings o sin candidatos.
        raise LLMProviderError(
            "Gemini no devolvió contenido (respuesta vacía o bloqueada)."
        ) from exc
    if not text or not text.strip():
        raise LLMProviderError(
            "Gemini devolvió una respuesta vacía para el resumen."
        )
    return text
