"""Palabras desde YouTube: el estudiante manda un enlace, el bot lee los subtítulos
del video y le propone palabras y estructuras de su nivel para guardar."""
import asyncio
import logging
import re

import httpx

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
MAX_CHARS = 3000     # tamaño de cada parte del video que se le pasa a la IA
MAX_ITEMS = 12


def video_id(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(1) if m else None


def _e(text) -> str:
    import html
    return html.escape(str(text or ""))


def _fetch_parts(vid: str, lang: str) -> list[dict]:
    """Descarga los subtítulos y los corta en partes de unos MAX_CHARS caracteres,
    guardando el minuto en que empieza y termina cada parte."""
    fetched = YouTubeTranscriptApi().fetch(vid, languages=SUB_LANGS[lang])
    parts, buf, size, begin, last = [], [], 0, None, 0.0
    for snip in fetched:
        line = snip.text.replace("\n", " ").strip()
        if not line:
            continue
        if begin is None:
            begin = snip.start
        buf.append(line)
        size += len(line) + 1
        last = snip.start + getattr(snip, "duration", 0)
        if size >= MAX_CHARS:
            parts.append({"text": " ".join(buf), "start": begin, "end": last})
            buf, size, begin = [], 0, None
    if buf:
        parts.append({"text": " ".join(buf), "start": begin, "end": last})
    return parts


async def video_title(vid: str) -> str:
    """Nombre del video, para el catálogo de canciones (vacío si no se pudo leer)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            )
        if r.status_code == 200:
            return str(r.json().get("title", "")).strip()
    except Exception:
        log.warning("No pude leer el título de %s", vid)
    return ""


def start_seconds(text: str) -> int:
    """Lee el minuto del enlace (…&t=754 o ?t=12m34s), si lo tiene."""
    m = re.search(r"[?&]t=(?:(\d+)h)?(?:(\d+)m)?(\d+)?s?", text or "")
    if not m or not any(m.groups()):
        return 0
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def _mmss(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


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


def _keyboard(pending: dict) -> InlineKeyboardMarkup:
    items = pending["items"]
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
    if db.get_song_by_video(pending["uid"], pending["vid"]):
        rows.append([InlineKeyboardButton("🎵 Está en tus canciones", callback_data=f"yts:{pending['vid']}")])
    else:
        rows.append([InlineKeyboardButton("🎵 Guardar como canción", callback_data=f"yts:{pending['vid']}")])
    if pending["index"] + 1 < pending["total"]:
        rows.append([InlineKeyboardButton(
            f"➡️ Parte {pending['index'] + 2} de {pending['total']}",
            callback_data=f"ytn:{pending['vid']}:{pending['index'] + 1}")])
    return InlineKeyboardMarkup(rows)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, vid: str, link: str = "") -> None:
    lang = user["language"]
    msg = update.effective_message
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    target = "chino" if lang == "zh" else "inglés"

    try:
        parts = await asyncio.to_thread(_fetch_parts, vid, lang)
    except Exception as exc:
        log.warning("Sin subtítulos para %s: %s", vid, type(exc).__name__)
        await msg.reply_text(
            f"😕 No encontré subtítulos en {target} para ese video. "
            "Solo funciona con videos que tengan subtítulos (aunque sean automáticos) en ese idioma."
        )
        return
    if not parts:
        await msg.reply_text("El video no tiene texto en los subtítulos 🤔")
        return

    # Si el enlace trae un minuto (?t=…), empieza desde la parte que lo contiene.
    t = start_seconds(link)
    index = next((i for i, p in enumerate(parts) if p["end"] > t), 0) if t else 0
    title = await video_title(vid)
    context.user_data.setdefault("ytvideo", {})[vid] = {"parts": parts, "lang": lang, "suggested": [], "title": title}
    if len(parts) > 1:
        await msg.reply_text(
            f"📺 Es un video largo: lo dividí en {len(parts)} partes. Empiezo por la {index + 1} "
            f"(desde {_mmss(parts[index]['start'])})."
        )
    else:
        await msg.reply_text("📺 Leyendo los subtítulos… dame unos segundos.")
    await analyze_part(update.effective_chat.id, context, user, vid, index)


async def analyze_part(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user: dict, vid: str, index: int) -> None:
    video = context.user_data.get("ytvideo", {}).get(vid)
    if not video or index >= len(video["parts"]):
        await context.bot.send_message(chat_id, "Ese video ya no está en memoria. Mándame el enlace otra vez.")
        return
    lang, parts = video["lang"], video["parts"]
    part = parts[index]
    text = part["text"]
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    # No repetir lo que ya sabe ni lo que ya le propuse en otras partes del mismo video.
    known = [w["word"] for w in db.all_words(user["id"], lang) if w["word"] in text] + video["suggested"]
    try:
        data = await ai.ask_json(extract_system(lang, db.level_of(user), known), text, temperature=0.3)
    except Exception:
        log.exception("Error analizando el video")
        await context.bot.send_message(
            chat_id, "😕 No pude conectar con la IA (quizá llegaste al límite por minuto). Espera un momento y prueba otra vez.",
            reply_markup=_next_button(vid, index, parts, retry=True),
        )
        return

    items = []
    for w in data.get("words") or []:
        if isinstance(w, dict) and str(w.get("word", "")).strip() and not db.find_word(user["id"], lang, w["word"]):
            items.append({**w, "kind": "word"})
    for st in data.get("structures") or []:
        if isinstance(st, dict) and str(st.get("pattern", "")).strip() and not db.find_structure(user["id"], lang, st["pattern"]):
            items.append({**st, "kind": "struct"})
    items = [it for it in items if (it.get("word") or it.get("pattern")) not in video["suggested"]][:MAX_ITEMS]
    video["suggested"] += [it.get("word") or it.get("pattern") for it in items]

    header = f"📺 <b>Parte {index + 1} de {len(parts)}</b> · {_mmss(part['start'])}–{_mmss(part['end'])}\n" if len(parts) > 1 else ""
    if not items:
        await context.bot.send_message(
            chat_id, header + "En esta parte no encontré nada nuevo para ti 💪",
            parse_mode=ParseMode.HTML, reply_markup=_next_button(vid, index, parts),
        )
        return

    lines = [header] if header else []
    if data.get("summary"):
        lines.append(f"<i>{_e(data['summary'])}</i>\n")
    for it in items:
        if it["kind"] == "word":
            lines.append(f"• <b>{_e(it['word'])}</b> <i>{_e(it.get('reading'))}</i> – {_e(it.get('meaning'))}")
        else:
            lines.append(f"• 📌 <b>{_e(it['pattern'])}</b> – {_e(it.get('explanation'))}")
    lines.append("\nToca las que quieras guardar:")

    pending = {"items": items, "lang": lang, "vid": vid, "index": index, "total": len(parts), "uid": user["id"]}
    sent = await context.bot.send_message(
        chat_id, "\n".join(lines)[:4000], parse_mode=ParseMode.HTML, reply_markup=_keyboard(pending)
    )
    context.user_data.setdefault("yt", {})[sent.message_id] = pending


def _next_button(vid: str, index: int, parts: list, retry: bool = False) -> InlineKeyboardMarkup | None:
    if retry:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Reintentar", callback_data=f"ytn:{vid}:{index}")]])
    if index + 1 < len(parts):
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            f"➡️ Parte {index + 2} de {len(parts)}", callback_data=f"ytn:{vid}:{index + 1}")]])
    return None


async def on_next(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, value: str) -> None:
    query = update.callback_query
    vid, _, idx = value.rpartition(":")
    await query.edit_message_reply_markup(None)
    await analyze_part(query.message.chat_id, context, user, vid, int(idx))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, value: str, today) -> None:
    query = update.callback_query
    pending = context.user_data.get("yt", {}).get(query.message.message_id)
    if not pending:
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(query.message.chat_id, "Esa lista ya expiró. Mándame el enlace otra vez.")
        return
    if value == "next":
        return
    items, lang = pending["items"], pending["lang"]
    targets = range(len(items)) if value == "all" else [int(value)]
    for i in targets:
        it = items[i]
        if it.get("saved"):
            continue
        if it["kind"] == "word":
            fields = {k: it.get(k, "") for k in ("word", "reading", "meaning", "example", "example_translation", "level")}
            db.add_word(user["id"], lang, today, source="YouTube", video_id=pending["vid"], **fields)
        else:
            fields = {k: it.get(k, "") for k in ("pattern", "explanation", "example", "example_reading", "example_translation", "level")}
            db.add_structure(user["id"], lang, source="YouTube", video_id=pending["vid"], **fields)
        it["saved"] = True
    db.touch_study(user["id"], today)
    await query.edit_message_reply_markup(_keyboard(pending))


async def on_save_song(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, vid: str) -> None:
    """Agrega el video al catálogo de canciones."""
    query = update.callback_query
    if db.get_song_by_video(user["id"], vid):
        await context.bot.send_message(query.message.chat_id, "🎵 Esa canción ya está en tu catálogo. Mírala con /canciones.")
        return
    video = context.user_data.get("ytvideo", {}).get(vid, {})
    title = video.get("title") or await video_title(vid)
    db.add_song(user["id"], video.get("lang") or user["language"], vid, title)
    pending = context.user_data.get("yt", {}).get(query.message.message_id)
    if pending:
        await query.edit_message_reply_markup(_keyboard(pending))
    await context.bot.send_message(
        query.message.chat_id,
        f"🎵 Guardé «{title or 'la canción'}» en tu catálogo. Las palabras que guardes de este video quedan "
        "en la canción. Para estudiarla: /canciones",
    )


async def analyze_song(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user: dict, vid: str) -> None:
    """Vuelve a revisar una canción del catálogo para buscar más palabras."""
    lang = user["language"]
    video = context.user_data.get("ytvideo", {}).get(vid)
    if not video:
        try:
            parts = await asyncio.to_thread(_fetch_parts, vid, lang)
        except Exception:
            await context.bot.send_message(chat_id, "😕 No pude leer los subtítulos de esa canción ahora.")
            return
        song = db.get_song_by_video(user["id"], vid)
        context.user_data.setdefault("ytvideo", {})[vid] = {
            "parts": parts, "lang": lang, "suggested": [], "title": song["title"] if song else "",
        }
    await analyze_part(chat_id, context, user, vid, 0)
