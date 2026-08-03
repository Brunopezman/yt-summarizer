# yt-summarizer

Herramienta de línea de comandos que descarga la transcripción de un video de YouTube y genera un resumen usando IA.

## Qué hace

1. Recibe una URL (o ID) de video de YouTube.
2. Descarga la transcripción disponible (automática o subida por el canal) con `youtube-transcript-api`.
3. Envía el texto a un LLM (OpenAI o Gemini) para generar un resumen.
4. Muestra el resumen en consola y opcionalmente lo guarda en un archivo (`.md` / `.txt`).

## Stack

- **Python 3.11+**
- [`youtube-transcript-api`](https://pypi.org/project/youtube-transcript-api/) — extracción de transcripciones
- [`typer`](https://typer.tiangolo.com/) — interfaz de línea de comandos
- **OpenAI API** o **Google Gemini SDK** — generación del resumen (proveedor configurable)
- `python-dotenv` — manejo de variables de entorno / API keys

## Instalación

> En Debian/Ubuntu, el comando `python` no existe por defecto (solo `python3`) y
> puede faltar el paquete `python3-venv`. Si `python3 -m venv` falla, instalalo
> con `sudo apt-get install -y python3-venv`.

```bash
git clone <repo-url>
cd yt-summarizer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env  # completar con tu API key
```

Dependencias opcionales por proveedor LLM:

```bash
pip install -e ".[openai]"   # SDK de OpenAI
pip install -e ".[gemini]"   # SDK de Google Gemini
```

## Uso

El paquete se invoca como subcomando (`yt-summarizer resumir <url>`) o como
módulo:

```bash
yt-summarizer resumir "https://www.youtube.com/watch?v=VIDEO_ID"
python -m yt_summarizer resumir "URL"  # equivalente
python -m yt_summarizer.cli resumir "URL"

# opciones
yt-summarizer resumir "URL" --idioma es --largo corto
yt-summarizer resumir "URL" --output resumen.md
yt-summarizer resumir "URL" --proveedor gemini
```

### Flags del comando `resumir`

| Flag | Default | Descripción |
|---|---|---|
| `--idioma, -i` | `es` | Idioma de la transcripción (código ISO 639-1, ej. `es`, `en`) |
| `--largo, -l` | `medio` | Nivel de detalle del resumen: `corto`, `medio` o `extenso` |
| `--output, -o` | — | Guarda el resumen en un archivo, además de mostrarlo |
| `--proveedor, -p` | el de `LLM_PROVIDER` | Proveedor LLM: `openai` o `gemini` |

La URL puede ser de cualquier formato de YouTube: `watch?v=`, `youtu.be/`,
`/shorts/`, `/embed/`, `/live/`, con o sin query params, o directamente un ID
de 11 caracteres.

### Salida y exit codes

- El resumen va a **stdout** (pipeable: `yt-summarizer resumir URL > resumen.txt`);
  el progreso, las confirmaciones y los errores van a **stderr**.
- Errores de uso (URL o flag inválido) → exit code `2`.
- Errores de negocio o configuración (transcripción, LLM, API keys) → exit
  code `1`, siempre con un mensaje claro en español y sin traceback.

## Configuración

Variables de entorno (`.env`):

| Variable | Descripción |
|---|---|
| `LLM_PROVIDER` | `openai` o `gemini` (default: `openai`) |
| `OPENAI_API_KEY` | API key de OpenAI (si se usa ese proveedor) |
| `GEMINI_API_KEY` | API key de Google Gemini (si se usa ese proveedor) |

## Estructura del proyecto

```
yt-summarizer/
├── yt_summarizer/
│   ├── __init__.py
│   ├── __main__.py     # python -m yt_summarizer
│   ├── cli.py          # comandos Typer
│   ├── transcript.py   # descarga y limpieza de transcripción
│   ├── summarizer.py   # abstracción de proveedor LLM (OpenAI/Gemini)
│   └── config.py       # carga de variables de entorno
├── tests/
├── .env.example
└── README.md
```

## Roadmap

- [x] CLI básica con Typer (extracción + resumen)
- [x] Soporte multi-proveedor LLM (OpenAI / Gemini)
- [ ] Manejo de videos sin transcripción disponible
- [x] Resúmenes con distintos niveles de detalle (corto / extenso / bullet points)
- [ ] Interfaz web opcional con Streamlit
- [x] Tests unitarios

## Licencia

MIT
