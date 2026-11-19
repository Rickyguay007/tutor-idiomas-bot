"""Voz: Whisper (voz → texto) y Edge TTS (texto → voz, gratis)."""
import asyncio
import os
import tempfile

import edge_tts
from openai import AsyncOpenAI

from . import config

VOICES = {
    "zh": os.getenv("VOICE_ZH", "zh-CN-XiaoxiaoNeural"),
    "en": os.getenv("VOICE_EN", "en-US-AvaNeural"),
    "es": os.getenv("VOICE_ES", "es-EC-AndreaNeural"),
}

_stt = (
    AsyncOpenAI(api_key=config.STT_API_KEY, base_url=config.STT_BASE_URL)
    if config.STT_API_KEY
    else None
)


def stt_enabled() -> bool:
    return _stt is not None


async def transcribe(path: str, lang: str | None = None) -> str:
    """Convierte una nota de voz (.ogg) en texto."""
    if _stt is None:
        raise RuntimeError("Falta STT_API_KEY en el .env")
    with open(path, "rb") as audio:
        kwargs = {"model": config.STT_MODEL, "file": audio}
        if lang in ("zh", "en", "es"):
            kwargs["language"] = lang
        result = await _stt.audio.transcriptions.create(**kwargs)
    return result.text.strip()


async def synthesize(text: str, lang: str) -> str:
    """Genera una nota de voz .ogg (opus) y devuelve su ruta. Quien llama la borra."""
    voice = VOICES.get(lang, VOICES["zh"])
    fd, mp3 = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    ogg = mp3[:-4] + ".ogg"
    try:
        await edge_tts.Communicate(text, voice, rate="-10%").save(mp3)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
            "-c:a", "libopus", "-b:a", "48k", ogg,
        )
        if await proc.wait() != 0:
            raise RuntimeError("ffmpeg no pudo convertir el audio")
        return ogg
    finally:
        if os.path.exists(mp3):
            os.remove(mp3)
