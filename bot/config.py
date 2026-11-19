"""Configuración leída del archivo .env (cada instalación tiene el suyo)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable {name} en el archivo .env (mira .env.example)")
    return value


TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")

# IA que hace de profesora. Cualquier servicio compatible con la API de OpenAI:
# DeepSeek, Qwen (DashScope), Gemini, Groq, OpenAI, Ollama local, etc.
LLM_API_KEY = _required("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# Voz a texto (Whisper). Groq lo ofrece gratis con límites diarios.
STT_API_KEY = os.getenv("STT_API_KEY", "").strip()
STT_BASE_URL = os.getenv("STT_BASE_URL", "https://api.groq.com/openai/v1")
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3")

DB_PATH = os.getenv("DB_PATH", "data/idiomas.db")
TIMEZONE = os.getenv("TIMEZONE", "America/Guayaquil")
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "8"))

# IDs de Telegram autorizados, separados por coma. Vacío = cualquiera puede usarlo
# (cuidado: cada mensaje gasta créditos de TU clave de IA).
ALLOWED_USERS = {
    int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x
}
