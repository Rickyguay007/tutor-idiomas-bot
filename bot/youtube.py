"""Palabras desde YouTube: el estudiante manda un enlace, el bot lee los subtítulos
del video y le propone palabras y estructuras de su nivel para guardar."""
import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes
from youtube_transcript_api import YouTubeTranscriptApi

from . import ai, db
from .prompts import LANG_NAMES

log = logging.getLogger(__name__)

URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
SUB_LANGS = {
    "zh": ["zh-Hans", "zh-CN", "zh", "zh-SG", "zh-Hant", "zh-TW", "zh-HK"],
    "en": ["en", "en-US", "en-GB"],
}
MAX_CHARS = 3500     # cuánto texto del video se le pasa a la IA
MAX_ITEMS = 12


def video_id(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(1) if m else None


def _e(text) -> str:
    import html
    return html.escape(str(text or ""))


def _fetch_subtitles(vid: str, lang: str) -> str:
    fetched = YouTubeTranscriptApi().fetch(vid, languages=SUB_LANGS[lang])
    return " ".join(s.text.replace("\n", " ") for s in fetched).strip()


def extract_system(lang: str, level: str, known: list[str]) -> str:
    reading = "pinyin con marcas de tono" if lang == "zh" else "transcripción IPA"
    levels = "HSK1 a HSK6" if lang == "zh" else "A1 a C2"
    script = " Escribe todo en caracteres simplificados aunque el subtítulo venga en tradicionales." if lang == "zh" else ""
    known_txt = ", ".join(known[:150]) or "(ninguna)"
    return f"""Eres profesora de {LANG_NAMES[lang]} para un hispanohablante de nivel {level}.
Te paso los subtítulos de un video de YouTube. Elige lo más útil para que lo aprenda:
- hasta 10 palabras o expresiones de su nivel o un poco más altas, frecuentes y útiles (no nombres propios ni palabras muy básicas);
- hasta 3 estructuras gramaticales que aparezcan en el video.
NO incluyas estas palabras, que ya conoce: {known_txt}
Los subtítulos automáticos pueden tener errores: ignora lo que no tenga sentido.{script}
Devuelve SOLO un objeto JSON:
{{
  "summary": "de qué trata el video, en español, 1 frase",
  "words": [{{"word": "", "reading": "{reading}", "meaning": "significado en español", "example": "la frase del video donde aparece (corta)", "example_translation": "traducción al español", "level": "{levels}"}}],
  "structures": [{{"pattern": "ej. 虽然…但是…", "explanation": "para qué sirve, en español", "example": "frase del video", "example_reading": "{reading}", "example_translation": "", "level": ""}}]
}}"""


def _keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, it in enumerate(items):
        label = it["word"] if it["kind"] == "word" else "📌 " + it["pattern"]
        label = ("✔ " if it.get("saved") else "➕ ") + label
        row.append(InlineKeyboardButton(label[:30], callback_data=f"yt:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if any(not it.get("saved") for it in items):
        rows.append([InlineKeyboardButton("✅ Guardar todo", callback_data="yt:all")])
    return InlineKeyboardMarkup(rows)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, vid: str) -> None:
    lang = user["language"]
    level = db.level_of(user)
    msg = update.effective_message
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    target = "chino" if lang == "zh" else "inglés"

    try:
        text = await asyncio.to_thread(_fetch_subtitles, vid, lang)
    except Exception as exc:
        log.warning("Sin subtítulos para %s: %s", vid, type(exc).__name__)
        await msg.reply_text(
            f"😕 No encontré subtítulos en {target} para ese video. "
            "Solo funciona con videos que tengan subtítulos (aunque sean automáticos) en ese idioma."
        )
        return
    if not text:
        await msg.reply_text("El video no tiene texto en los subtítulos 🤔")
        return

    await msg.reply_text("📺 Leyendo los subtítulos… dame unos segundos.")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    known = [w["word"] for w in db.all_words(user["id"], lang) if w["word"] in text]
    try:
        data = await ai.ask_json(extract_system(lang, level, known), text[:MAX_CHARS], temperature=0.3)
    except Exception:
        log.exception("Error analizando el video")
        await msg.reply_text("😕 No pude conectar con la IA. Intenta de nuevo.")
        return

    items = []
    for w in data.get("words") or []:
        if isinstance(w, dict) and str(w.get("word", "")).strip() and not db.find_word(user["id"], lang, w["word"]):
            items.append({**w, "kind": "word"})
    for st in data.get("structures") or []:
        if isinstance(st, dict) and str(st.get("pattern", "")).strip() and not db.find_structure(user["id"], lang, st["pattern"]):
            items.append({**st, "kind": "struct"})
    items = items[:MAX_ITEMS]
    if not items:
        await msg.reply_text("Revisé el video y no encontré nada nuevo para ti. ¡Ya conoces todo lo importante! 💪")
        return

    lines = []
    if data.get("summary"):
        lines.append(f"📺 <i>{_e(data['summary'])}</i>\n")
    for it in items:
        if it["kind"] == "word":
            lines.append(f"• <b>{_e(it['word'])}</b> <i>{_e(it.get('reading'))}</i> – {_e(it.get('meaning'))}")
        else:
            lines.append(f"• 📌 <b>{_e(it['pattern'])}</b> – {_e(it.get('explanation'))}")
    if len(text) > MAX_CHARS:
        lines.append("\n<i>(Revisé el comienzo del video; es largo.)</i>")
    lines.append("\nToca las que quieras guardar:")

    sent = await msg.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.HTML, reply_markup=_keyboard(items))
    context.user_data.setdefault("yt", {})[sent.message_id] = {"items": items, "lang": lang, "vid": vid}


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, value: str, today) -> None:
    query = update.callback_query
    pending = context.user_data.get("yt", {}).get(query.message.message_id)
    if not pending:
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(query.message.chat_id, "Esa lista ya expiró. Mándame el enlace otra vez.")
        return
    items, lang = pending["items"], pending["lang"]
    targets = range(len(items)) if value == "all" else [int(value)]
    for i in targets:
        it = items[i]
        if it.get("saved"):
            continue
        if it["kind"] == "word":
            fields = {k: it.get(k, "") for k in ("word", "reading", "meaning", "example", "example_translation", "level")}
            db.add_word(user["id"], lang, today, source="YouTube", **fields)
        else:
            fields = {k: it.get(k, "") for k in ("pattern", "explanation", "example", "example_reading", "example_translation", "level")}
            db.add_structure(user["id"], lang, source="YouTube", **fields)
        it["saved"] = True
    db.touch_study(user["id"], today)
    await query.edit_message_reply_markup(_keyboard(items))
