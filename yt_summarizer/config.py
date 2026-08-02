"""Carga de configuración del proyecto (variables de entorno y ``.env``).

Centraliza la lectura de las variables que gobiernan el comportamiento de la
CLI y la selección del proveedor LLM:

- ``LLM_PROVIDER`` — proveedor por defecto (``openai`` | ``gemini``);
- ``OPENAI_API_KEY`` — API key del proveedor OpenAI;
- ``GEMINI_API_KEY`` — API key del proveedor Gemini.

La carga se hace con ``python-dotenv`` (si está instalado) y nunca pisa
variables ya definidas en el entorno real: un valor seteado con ``export``
tiene prioridad sobre el ``.env``.

La validación de las API keys vive acá para cumplir el principio fail-fast:
la CLI chequea la key del proveedor elegido con :func:`get_api_key` ANTES de
tocar red (descargar la transcripción o llamar al LLM), y el error dice
explícitamente qué variable falta, nunca un traceback crudo.

Los errores propios de esta capa se agrupan bajo :class:`ConfigError`.

Ejemplo de uso::

    from yt_summarizer.config import get_api_key, get_provider

    proveedor = get_provider()        # LLM_PROVIDER o "openai"
    key = get_api_key(proveedor)      # ConfigError si falta la key
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Union

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv es opcional
    load_dotenv = None

__all__ = [
    "VALID_PROVIDERS",
    "DEFAULT_PROVIDER",
    "ENV_VAR_FOR_PROVIDER",
    "ConfigError",
    "load_env",
    "get_provider",
    "get_api_key",
]

#: Proveedores LLM soportados por la herramienta.
VALID_PROVIDERS: Final[tuple[str, ...]] = ("openai", "gemini")

#: Proveedor usado cuando no se define ``LLM_PROVIDER`` ni ``--proveedor``.
DEFAULT_PROVIDER: Final[str] = "openai"

#: Variable de entorno que guarda la API key de cada proveedor.
ENV_VAR_FOR_PROVIDER: Final[dict[str, str]] = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class ConfigError(Exception):
    """Error de configuración: falta una variable de entorno o es inválida.

    Lo lanzan :func:`get_provider` (proveedor inválido) y :func:`get_api_key`
    (proveedor inválido o API key faltante). La CLI lo captura y lo traduce a
    un mensaje de consola claro, con exit code distinto de 0.
    """


def load_env(path: Union[str, Path, None] = None) -> None:
    """Carga las variables de ``.env`` al entorno sin pisar las ya definidas.

    Busca en dos lugares (en este orden, el primero gana):

    1. el ``.env`` del directorio de trabajo actual, si existe;
    2. el ``.env`` de la raíz del proyecto (dos niveles arriba de este
       módulo), para que el comando funcione desde cualquier directorio.

    Si ``python-dotenv`` no está instalado, no hace nada: las variables se
    leen igual del entorno real.
    """
    if load_dotenv is None:
        return
    load_dotenv(dotenv_path=path, override=False)
    if path is None:
        raiz_proyecto = Path(__file__).resolve().parent.parent
        load_dotenv(dotenv_path=raiz_proyecto / ".env", override=False)


def get_provider(provider: Union[str, None] = None) -> str:
    """Resuelve el proveedor LLM a usar, en este orden.

    ``provider`` (el flag ``--proveedor``) → ``LLM_PROVIDER`` (del entorno o
    del ``.env``) → :data:`DEFAULT_PROVIDER` (``"openai"``). El valor se
    normaliza a minúsculas y sin espacios alrededor.

    Args:
        provider: nombre del proveedor (``"openai"`` o ``"gemini"``). Si es
            ``None``, se usa ``LLM_PROVIDER`` o el default.

    Returns:
        El nombre normalizado del proveedor.

    Raises:
        ConfigError: si el proveedor no es uno de :data:`VALID_PROVIDERS`.
    """
    nombre = (
        provider or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER
    ).strip().lower()
    if nombre not in VALID_PROVIDERS:
        opciones = ", ".join(f"'{p}'" for p in VALID_PROVIDERS)
        raise ConfigError(
            f"Proveedor LLM inválido: '{nombre}'. Valores válidos: {opciones}. "
            "Configuralo con la variable de entorno LLM_PROVIDER o con el "
            "flag --proveedor."
        )
    return nombre


def get_api_key(provider: Union[str, None] = None) -> str:
    """Devuelve la API key del proveedor elegido; error claro si falta.

    Valida la key ANTES de cualquier llamada de red, para fallar rápido con
    un mensaje que dice exactamente qué variable de entorno falta.

    Args:
        provider: nombre del proveedor (``"openai"`` o ``"gemini"``). Si es
            ``None``, se resuelve con :func:`get_provider` (``LLM_PROVIDER``
            → default).

    Returns:
        La API key del proveedor elegido.

    Raises:
        ConfigError: si el proveedor es inválido o si falta la variable de
            entorno correspondiente (``OPENAI_API_KEY`` o ``GEMINI_API_KEY``).
    """
    nombre = get_provider(provider)
    env_var = ENV_VAR_FOR_PROVIDER[nombre]
    key = os.environ.get(env_var, "").strip()
    if not key:
        raise ConfigError(
            f"Falta la variable de entorno '{env_var}', requerida por el "
            f"proveedor '{nombre}'. Copiá '.env.example' a '.env' y completala, "
            "o elegí otro proveedor con --proveedor / LLM_PROVIDER."
        )
    return key


# Carga el `.env` (LLM_PROVIDER, *_API_KEY) al importar el módulo: así la CLI
# y cualquier consumidor de la config ven las variables sin pasos extra. Con
# override=False las variables ya definidas en el entorno real tienen prioridad.
load_env()
