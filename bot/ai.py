"""Conexión con la IA (cualquier proveedor compatible con la API de OpenAI)."""
import json
import re

from openai import AsyncOpenAI

from . import config, prompts

_client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


def parse_json(text: str) -> dict:
    """Extrae el JSON aunque la IA lo envuelva en ```json ... ``` o agregue texto."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


async def _ask_json(messages: list[dict], temperature: float = 0.7) -> dict:
    kwargs = dict(model=config.LLM_MODEL, messages=messages, temperature=temperature)
    try:
        resp = await _client.chat.completions.create(
            response_format={"type": "json_object"}, **kwargs
        )
    except Exception:
        # Algunos proveedores no aceptan response_format; el prompt ya pide JSON.
        resp = await _client.chat.completions.create(**kwargs)
    return parse_json(resp.choices[0].message.content)


async def tutor_reply(
    lang: str, level: str, name: str, history: list[dict], mistakes: list[dict],
    text: str, from_voice: bool = False,
) -> dict:
    system = prompts.tutor_system(lang, level, name, mistakes)
    user_text = f"[nota de voz transcrita] {text}" if from_voice else text
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
    data = await _ask_json(messages)
    data.setdefault("reply", "")
    data.setdefault("reply_language", lang)
    for key in ("corrections", "new_words", "save_words"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


async def lookup_word(lang: str, level: str, word: str) -> dict:
    messages = [
        {"role": "system", "content": prompts.word_lookup_system(lang, level)},
        {"role": "user", "content": word},
    ]
    return await _ask_json(messages, temperature=0.3)
