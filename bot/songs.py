"""Catálogo de canciones: estudiar solo las palabras y estructuras de una canción.

Una canción es un video de YouTube que el estudiante marcó con «🎵 Guardar como canción».
Las palabras que guarda desde ese video quedan vinculadas a la canción.
"""
import html
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from . import db

PER_PAGE = 8


def _e(text) -> str:
    return html.escape(str(text or ""))


def _short(title: str, n: int = 40) -> str:
    return title if len(title) <= n else title[: n - 1] + "…"


async def catalog(chat_id: int, user: dict, context: ContextTypes.DEFAULT_TYPE, page: int = 0, edit=None) -> None:
    songs = db.list_songs(user["id"], user["language"])
    if not songs:
        text = (
            "🎵 Todavía no tienes canciones.\n\n"
            "Mándame el enlace de YouTube de una canción con subtítulos y toca «🎵 Guardar como canción». "
            "Después guarda las palabras que te interesen y aquí podrás estudiarlas."
        )
        await context.bot.send_message(chat_id, text)
        return
    pages = (len(songs) - 1) // PER_PAGE + 1
    page = max(0, min(page, pages - 1))
    chunk = songs[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [
        [InlineKeyboardButton(f"🎵 {_short(s['title'])} · {s['n_words']}", callback_data=f"sg:{s['id']}:menu")]
        for s in chunk
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"sgl:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"sgl:{page + 1}"))
    if nav:
        rows.append(nav)
    text = f"🎵 <b>Tus canciones</b> ({len(songs)})\nEl número es cuántas palabras guardaste de cada una. Elige una:"
    markup = InlineKeyboardMarkup(rows)
    if edit:
        await edit.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)


def _menu_markup(song: dict) -> InlineKeyboardMarkup:
    sid = song["id"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Ver palabras", callback_data=f"sg:{sid}:words"),
         InlineKeyboardButton("🔁 Repasar", callback_data=f"sg:{sid}:review")],
        [InlineKeyboardButton("❓ Quiz", callback_data=f"sg:{sid}:quiz"),
         InlineKeyboardButton("🔤 Traducir y oración", callback_data=f"sg:{sid}:practice")],
        [InlineKeyboardButton("📌 Usar sus estructuras", callback_data=f"sg:{sid}:struct"),
         InlineKeyboardButton("➕ Buscar más palabras", callback_data=f"sg:{sid}:more")],
        [InlineKeyboardButton("▶️ Abrir en YouTube", url=f"https://www.youtube.com/watch?v={song['video_id']}")],
        [InlineKeyboardButton("⬅️ Catálogo", callback_data="sgl:0"),
         InlineKeyboardButton("🗑 Quitar", callback_data=f"sg:{sid}:delask")],
    ])


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, action: str, value: str, today,
                    send_quiz, start_word, start_structure, send_next_card, analyze_video) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    uid = user["id"]

    if action == "sgl":
        await catalog(chat_id, user, context, page=int(value), edit=query)
        return

    sid, _, what = value.partition(":")
    song = db.get_song(int(sid), uid)
    if not song:
        await query.edit_message_text("Esa canción ya no está en tu catálogo.")
        return
    vid = song["video_id"]
    words = db.song_words(uid, vid)
    structs = db.song_structures(uid, vid)

    if what == "menu":
        await query.edit_message_text(
            f"🎵 <b>{_e(song['title'])}</b>\n📚 {len(words)} palabras · 📌 {len(structs)} estructuras\n\n¿Qué quieres hacer?",
            parse_mode=ParseMode.HTML, reply_markup=_menu_markup(song),
        )

    elif what == "words":
        if not words and not structs:
            await context.bot.send_message(chat_id, "Todavía no guardaste palabras de esta canción. Toca «➕ Buscar más palabras».")
            return
        lines = [f"🎵 <b>{_e(song['title'])}</b>", ""]
        lines += [f"• <b>{_e(w['word'])}</b> <i>{_e(w['reading'])}</i> – {_e(w['meaning'])}" for w in words]
        if structs:
            lines.append("")
            lines += [f"• 📌 <b>{_e(s['pattern'])}</b> – {_e(s['explanation'])}" for s in structs]
        await context.bot.send_message(chat_id, "\n".join(lines)[:4000], parse_mode=ParseMode.HTML)

    elif what == "review":
        if not words:
            await context.bot.send_message(chat_id, "No hay palabras de esta canción para repasar todavía.")
            return
        context.user_data["review_song"] = {"vid": vid, "title": song["title"], "seen": []}
        await send_next_card(chat_id, uid, user["language"], context)

    elif what == "quiz":
        pool = [w for w in words if w["meaning"]]
        if not pool:
            await context.bot.send_message(chat_id, "No hay palabras de esta canción para el quiz todavía.")
            return
        await send_quiz(chat_id, context, user, targets=pool)

    elif what == "practice":
        pool = [w for w in words if w["meaning"]]
        if not pool:
            await context.bot.send_message(chat_id, "No hay palabras de esta canción para practicar todavía.")
            return
        await start_word(chat_id, user, context, today, pool=pool)

    elif what == "struct":
        if not structs:
            await context.bot.send_message(chat_id, "Esta canción no tiene estructuras guardadas.")
            return
        await start_structure(chat_id, user, context, pool=structs)

    elif what == "more":
        await analyze_video(chat_id, context, user, vid)

    elif what == "delask":
        await query.edit_message_reply_markup(InlineKeyboardMarkup([[
            InlineKeyboardButton("Sí, quitarla", callback_data=f"sg:{sid}:del"),
            InlineKeyboardButton("No", callback_data=f"sg:{sid}:menu"),
        ]]))

    elif what == "del":
        db.delete_song(song["id"], uid)
        await query.edit_message_text(
            f"Quité «{song['title']}» del catálogo. Sus palabras siguen guardadas en tu vocabulario."
        )


def pick_review_word(uid: int, state: dict) -> dict | None:
    """Siguiente palabra de la canción en el repaso: primero las vencidas, sin repetir en la sesión."""
    words = [w for w in db.song_words(uid, state["vid"]) if w["id"] not in state["seen"]]
    if not words:
        return None
    word = words[0] if words[0]["due"] else random.choice(words)
    state["seen"].append(word["id"])
    return word
