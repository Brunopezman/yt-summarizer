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

```bash
git clone <repo-url>
cd yt-summarizer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # completar con tu API key
```

## Uso

```bash
python -m yt_summarizer resumir "https://www.youtube.com/watch?v=VIDEO_ID"

# opciones
python -m yt_summarizer resumir "URL" --idioma es --largo corto
python -m yt_summarizer resumir "URL" --output resumen.md
python -m yt_summarizer resumir "URL" --proveedor gemini
```

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
│   ├── cli.py          # comandos Typer
│   ├── transcript.py   # descarga y limpieza de transcripción
│   ├── summarizer.py   # abstracción de proveedor LLM (OpenAI/Gemini)
│   └── config.py       # carga de variables de entorno
├── tests/
├── requirements.txt
├── .env.example
└── README.md
```

## Roadmap

- [ ] CLI básica con Typer (extracción + resumen)
- [ ] Soporte multi-proveedor LLM (OpenAI / Gemini)
- [ ] Manejo de videos sin transcripción disponible
- [ ] Resúmenes con distintos niveles de detalle (corto / extenso / bullet points)
- [ ] Interfaz web opcional con Streamlit
- [ ] Tests unitarios

## Licencia

MIT
