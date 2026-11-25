"""Comandos y mensajes del bot."""
import csv
import functools
import html
import io
import logging
import os
import random
import tempfile
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Poll, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from . import ai, config, db, practice, speech, srs, youtube

log = logging.getLogger(__name__)
TZ = ZoneInfo(config.TIMEZONE)

LANG_LABEL = {"zh": "🇨🇳 Chino", "en": "🇬🇧 Inglés"}
LEVELS = {
    "zh": ["HSK1", "HSK2", "HSK3", "HSK4", "HSK5", "HSK6"],
    "en": ["A1", "A2", "B1", "B2", "C1", "C2"],
}

HELP = """<b>Tutor de idiomas</b> 🇨🇳🇬🇧

Escríbeme o mándame una <b>nota de voz</b> en el idioma que estás estudiando y conversamos. Si me escribes en español, te explico o te ayudo a decirlo.

<b>Configuración</b>
/idioma – cambiar entre chino e inglés
/nivel – tu nivel (HSK o A1–C2)
/voz – activar o quitar las respuestas en audio

<b>Vocabulario</b>
/agregar palabra – guardar una palabra (ej. <code>/agregar 坚持</code>)
También puedes pedírmelo por voz: <i>"guárdame la palabra jiānchí"</i>
/estructuras – ver tus estructuras gramaticales
Mándame un <b>enlace de YouTube</b> y te propongo palabras y estructuras del video para guardar
/ver – ver todas tus palabras y estructuras en una página con buscador
/palabras – ver las últimas guardadas
/buscar palabra – ver una palabra guardada
/exportar – descargar todo para Anki (CSV)

<b>Práctica</b>
/practicar – te digo una palabra en español, la traduces y haces una oración
/usar – haz una oración con una de tus estructuras
/describir – te muestro un dibujo y describes qué pasa
/pista – ayuda durante el ejercicio · /terminar – salir del ejercicio
/repasar – tarjetas con repaso espaciado
/quiz – pregunta rápida con tus palabras
/decir frase – escuchar cómo se pronuncia

<b>Progreso</b>
/stats – racha, palabras y repasos pendientes
/recordatorio 20:00 – aviso diario (o <code>/recordatorio off</code>)
/olvidar – reiniciar la conversación (no borra palabras)"""


def today() -> date:
    return datetime.now(TZ).date()


def restricted(func):
    """Solo deja pasar a los usuarios de ALLOWED_USERS (si está configurado)."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if config.ALLOWED_USERS and (user is None or user.id not in config.ALLOWED_USERS):
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"Este bot es privado. Tu ID de Telegram es {user.id if user else '?'}."
                )
            return
        return await func(update, context)

    return wrapper


def _user(update: Update) -> dict:
    tg = update.effective_user
    return db.get_user(tg.id, tg.first_name or "")


def e(text) -> str:
    return html.escape(str(text or ""))


def word_card(w: dict, with_srs: bool = False) -> str:
    lines = [f"<b>{e(w.get('word'))}</b>"]
    if w.get("reading"):
        lines[0] += f"  <i>{e(w['reading'])}</i>"
    if w.get("meaning"):
        lines.append(e(w["meaning"]))
    if w.get("example"):
        lines.append(f"\n📝 {e(w['example'])}")
        if w.get("example_translation"):
            lines.append(f"<i>{e(w['example_translation'])}</i>")
    if w.get("note"):
        lines.append(f"\n💡 {e(w['note'])}")
    extra = []
    if w.get("level"):
        extra.append(w["level"])
    if with_srs and w.get("due"):
        extra.append(f"próximo repaso {w['due']}")
    if extra:
        lines.append(f"\n<code>{e(' · '.join(extra))}</code>")
    return "\n".join(lines)


async def send_voice(update: Update, context, text: str, lang: str) -> None:
    if not text.strip():
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.RECORD_VOICE)
    path = await speech.synthesize(text, lang)
    try:
        with open(path, "rb") as f:
            await update.effective_message.reply_voice(f)
    finally:
        os.remove(path)


# ---------- configuración ----------

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    await update.message.reply_text(
        f"¡Hola{', ' + e(user['name']) if user['name'] else ''}! 你好! Hello!\n\n"
        f"Ahora estás practicando <b>{LANG_LABEL[user['language']]}</b> "
        f"en nivel <b>{db.level_of(user)}</b>.\n\n" + HELP,
        parse_mode=ParseMode.HTML,
    )


@restricted
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


@restricted
async def idioma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[InlineKeyboardButton(label, callback_data=f"lang:{code}")] for code, label in LANG_LABEL.items()]
    await update.message.reply_text("¿Qué quieres practicar?", reply_markup=InlineKeyboardMarkup(buttons))


@restricted
async def nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    lang = user["language"]
    levels = LEVELS[lang]
    rows = [levels[i:i + 3] for i in range(0, len(levels), 3)]
    buttons = [[InlineKeyboardButton(l, callback_data=f"lvl:{l}") for l in row] for row in rows]
    await update.message.reply_text(
        f"Tu nivel de {LANG_LABEL[lang]} es {db.level_of(user)}. Elige uno nuevo:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@restricted
async def voz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    new = 0 if user["voice"] else 1
    db.update_user(user["id"], voice=new)
    await update.message.reply_text(
        "🔊 Te responderé también con audio." if new else "🔇 Solo te responderé por texto."
    )


@restricted
async def olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    db.clear_history(user["id"], user["language"])
    await update.message.reply_text("Listo, empezamos una conversación nueva. Tus palabras siguen guardadas.")


# ---------- conversación ----------

async def _converse(update: Update, context, text: str, from_voice: bool = False):
    user = _user(update)
    uid, lang = user["id"], user["language"]
    level = db.level_of(user)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        data = await ai.tutor_reply(
            lang, level, user["name"],
            history=db.get_history(uid, lang, config.HISTORY_TURNS * 2),
            mistakes=db.recent_mistakes(uid, lang),
            text=text, from_voice=from_voice,
        )
    except Exception:
        log.exception("Error con la IA")
        await update.effective_message.reply_text(
            "😕 No pude conectar con la IA. Revisa LLM_API_KEY / LLM_BASE_URL o intenta de nuevo."
        )
        return

    reply = str(data.get("reply", "")).strip()
    db.add_history(uid, lang, "user", text)
    db.add_history(uid, lang, "assistant", reply)
    db.touch_study(uid, today())

    parts = [f"<b>{e(reply)}</b>"]
    if data.get("reading"):
        parts.append(f"<i>{e(data['reading'])}</i>")
    if data.get("translation"):
        parts.append(f"🇪🇸 {e(data['translation'])}")

    corrections = [c for c in data["corrections"] if isinstance(c, dict) and c.get("corrected")]
    if corrections:
        parts.append("\n✏️ <b>Correcciones</b>")
        for c in corrections:
            db.add_mistake(uid, lang, c.get("original", ""), c["corrected"], c.get("explanation", ""))
            line = f"• <s>{e(c.get('original'))}</s>"
            if c.get("original_reading"):
                line += f" <i>({e(c['original_reading'])})</i>"
            line += f"\n  → <b>{e(c['corrected'])}</b>"
            if c.get("corrected_reading"):
                line += f" <i>({e(c['corrected_reading'])})</i>"
            if c.get("explanation"):
                line += f"\n  {e(c['explanation'])}"
            parts.append(line)

    g = data["grammar"]
    if g.get("pattern"):
        is_new = db.add_structure(uid, lang, source="conversación", **g)
        parts.append(f"\n📌 <b>{e(g['pattern'])}</b>" + ("  <i>(guardada)</i>" if is_new else ""))
        if g.get("explanation"):
            parts.append(e(g["explanation"]))
        if g.get("example"):
            parts.append(f"<i>{e(g['example'])}</i>" + (f"  ({e(g['example_reading'])})" if g.get("example_reading") else ""))

    for st in data["save_structures"]:
        if isinstance(st, dict) and st.get("pattern"):
            if db.add_structure(uid, lang, source="pedido", **st):
                parts.append(f"\n✅ Guardé la estructura <b>{e(st['pattern'])}</b>")
            else:
                parts.append(f"\n👍 Ya tenías guardada <b>{e(st['pattern'])}</b>")

    requested, already = [], []
    for w in data["save_words"]:
        if not isinstance(w, dict) or not str(w.get("word", "")).strip():
            continue
        if db.add_word(uid, lang, today(), source="pedido", **w):
            requested.append(w)
        else:
            already.append(w)
    if requested:
        parts.append("\n✅ <b>Guardé lo que me pediste</b>")
        parts.extend(f"• {e(w['word'])} <i>{e(w.get('reading'))}</i> – {e(w.get('meaning'))}" for w in requested)
    if already:
        parts.append("\n👍 Ya tenías guardada: " + ", ".join(e(w["word"]) for w in already))

    saved = []
    for w in data["new_words"]:
        if isinstance(w, dict) and db.add_word(uid, lang, today(), source="conversación", **w):
            saved.append(w)
    if saved:
        parts.append("\n🆕 <b>Guardé</b>")
        parts.extend(f"• {e(w['word'])} <i>{e(w.get('reading'))}</i> – {e(w.get('meaning'))}" for w in saved)

    message = "\n".join(parts)
    if len(message) > 4000:
        message = message[:4000] + "…"
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)

    if user["voice"] and reply:
        try:
            await send_voice(update, context, reply, data.get("reply_language") or lang)
        except Exception:
            log.exception("Error generando la voz")


@restricted
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = youtube.video_id(update.message.text)
    if vid:
        await youtube.handle_link(update, context, _user(update), vid)
        return
    if context.user_data.get("ex"):
        await practice.answer(update, context, _user(update), update.message.text, today())
        return
    await _converse(update, context, update.message.text)


@restricted
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not speech.stt_enabled():
        await update.message.reply_text("Para usar notas de voz falta configurar STT_API_KEY en el .env.")
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    voice = update.message.voice or update.message.audio
    tg_file = await voice.get_file()
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        await tg_file.download_to_drive(path)
        text = await speech.transcribe(path)
    except Exception:
        log.exception("Error transcribiendo")
        await update.message.reply_text("😕 No pude entender el audio. ¿Lo intentas otra vez?")
        return
    finally:
        os.remove(path)

    if not text:
        await update.message.reply_text("No escuché nada en el audio 🤔")
        return
    await update.message.reply_text(f"🎙️ <i>{e(text)}</i>", parse_mode=ParseMode.HTML)
    if context.user_data.get("ex"):
        await practice.answer(update, context, _user(update), text, today())
        return
    await _converse(update, context, text, from_voice=True)


# ---------- vocabulario ----------

@restricted
async def agregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    word = " ".join(context.args).strip()
    if not word:
        await update.message.reply_text("Escribe la palabra después del comando, por ejemplo:\n/agregar 坚持")
        return
    lang = user["language"]
    existing = db.find_word(user["id"], lang, word)
    if existing:
        await update.message.reply_text(
            "Ya la tenías guardada 👇\n\n" + word_card(existing, with_srs=True), parse_mode=ParseMode.HTML
        )
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        info = await ai.lookup_word(lang, db.level_of(user), word)
    except Exception:
        log.exception("Error buscando palabra")
        info = {"word": word}

    info["word"] = (info.get("word") or word).strip()
    if db.find_word(user["id"], lang, info["word"]):
        await update.message.reply_text(f"Ya tenías guardada {info['word']} 👍")
        return
    db.add_word(user["id"], lang, today(), source="manual", **info)
    await update.message.reply_text("✅ Guardada\n\n" + word_card(info), parse_mode=ParseMode.HTML)
    if user["voice"]:
        try:
            await send_voice(update, context, f"{info['word']}。{info.get('example', '')}", lang)
        except Exception:
            log.exception("Error generando la voz")


@restricted
async def palabras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    lang = user["language"]
    level = context.args[0].upper() if context.args else None
    words = db.list_words(user["id"], lang, limit=25, level=level)
    if not words:
        await update.message.reply_text("Todavía no tienes palabras guardadas. Usa /agregar o conversa conmigo.")
        return
    total = db.count_words(user["id"], lang)
    lines = [f"<b>{LANG_LABEL[lang]} · {total} palabras</b>" + (f" (filtro {e(level)})" if level else ""), ""]
    lines += [f"• <b>{e(w['word'])}</b> <i>{e(w['reading'])}</i> – {e(w['meaning'])}" for w in words]
    if total > len(words):
        lines.append(f"\n…y {total - len(words)} más. Usa /ver para verlas todas.")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.HTML)


@restricted
async def estructuras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    lang = user["language"]
    items = db.list_structures(user["id"], lang, limit=15)
    if not items:
        await update.message.reply_text(
            "Todavía no tienes estructuras guardadas. Se guardan solas cuando conversamos, "
            "o pídemelo: «guárdame la estructura 虽然…但是…»."
        )
        return
    total = db.count_structures(user["id"], lang)
    lines = [f"<b>📌 {total} estructuras</b>", ""]
    for st in items:
        lines.append(f"<b>{e(st['pattern'])}</b>" + (f"  <code>{e(st['level'])}</code>" if st["level"] else ""))
        if st["explanation"]:
            lines.append(e(st["explanation"]))
        if st["example"]:
            lines.append(f"<i>{e(st['example'])}</i>")
        lines.append("")
    if total > len(items):
        lines.append(f"…y {total - len(items)} más. Usa /ver para verlas todas.")
    lines.append("Practícalas con /usar")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.HTML)


@restricted
async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    word = " ".join(context.args).strip()
    found = db.find_word(user["id"], user["language"], word) if word else None
    if not found:
        await update.message.reply_text("No la encontré entre tus palabras. Puedes guardarla con /agregar.")
        return
    await update.message.reply_text(word_card(found, with_srs=True), parse_mode=ParseMode.HTML)


@restricted
async def exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    lang = user["language"]
    words = db.all_words(user["id"], lang)
    if not words:
        await update.message.reply_text("No hay palabras para exportar todavía.")
        return
    buf = io.StringIO()
    writer = csv.writer(buf)
    for w in words:
        back = "<br>".join(
            x for x in (w["reading"], w["meaning"], w["example"], w["example_translation"]) if x
        )
        tags = " ".join(x for x in (f"tutor-{lang}", w["level"], w["source"].replace(" ", "_")) if x)
        writer.writerow([w["word"], back, tags])
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    data.name = f"palabras_{lang}_{today().isoformat()}.csv"
    await update.message.reply_document(
        data,
        caption=f"{len(words)} palabras. En Anki: Archivo → Importar, separado por comas, permitir HTML.",
    )


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mis palabras · {title}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#faf8f5;color:#222}}
h1{{font-size:1.3rem;margin:0 0 4px}} p.sub{{color:#666;margin:0 0 12px}}
input{{width:100%;box-sizing:border-box;padding:10px;font-size:1rem;border:1px solid #ccc;border-radius:8px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid #eee;vertical-align:top}}
th{{background:#f0ece6;position:sticky;top:0;cursor:pointer}}
td.w{{font-size:1.4rem;white-space:nowrap}} td.r{{color:#a33;font-style:italic}}
td.x{{color:#555;font-size:.9rem}} td.m{{color:#888;font-size:.8rem;white-space:nowrap}}
@media (max-width:600px){{td.x,th.x{{display:none}}}}
</style></head><body>
<h1>Mis palabras · {title}</h1>
<p class="sub">{count} palabras · actualizado {date}</p>
<input id="q" placeholder="Buscar palabra, pinyin o significado…">
<table><thead><tr><th>Palabra</th><th>Pronunciación</th><th>Significado</th><th class="x">Ejemplo</th><th>Nivel</th></tr></thead>
<tbody id="t">{rows}</tbody></table>
<h1 style="margin-top:28px">Estructuras gramaticales</h1>
<p class="sub">{scount} estructuras</p>
<table><thead><tr><th>Estructura</th><th>Para qué sirve</th><th>Ejemplo</th></tr></thead>
<tbody id="s">{srows}</tbody></table>
<script>
document.getElementById('q').addEventListener('input',function(){{
  var q=this.value.toLowerCase();
  document.querySelectorAll('#t tr, #s tr').forEach(function(r){{r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none'}});
}});
</script></body></html>"""


@restricted
async def ver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía una página con todas las palabras, que se abre en el navegador."""
    user = _user(update)
    lang = user["language"]
    words = list(reversed(db.all_words(user["id"], lang)))
    structs = db.list_structures(user["id"], lang)
    if not words and not structs:
        await update.message.reply_text("Todavía no tienes palabras guardadas.")
        return
    srows = "\n".join(
        f"<tr><td class='w' style='font-size:1.1rem'>{e(st['pattern'])}<br><span class='m'>{e(st['level'])}</span></td>"
        f"<td>{e(st['explanation'])}</td>"
        f"<td>{e(st['example'])}<br><span class='r'>{e(st['example_reading'])}</span><br><i>{e(st['example_translation'])}</i></td></tr>"
        for st in structs
    )
    rows = "\n".join(
        f"<tr><td class='w'>{e(w['word'])}</td><td class='r'>{e(w['reading'])}</td>"
        f"<td>{e(w['meaning'])}</td>"
        f"<td class='x'>{e(w['example'])}<br><i>{e(w['example_translation'])}</i></td>"
        f"<td class='m'>{e(w['level'])}<br>{e(w['source'])}<br>{e((w['created_at'] or '')[:10])}</td></tr>"
        for w in words
    )
    html_page = PAGE.format(
        title=e(LANG_LABEL[lang].split(" ", 1)[1]), count=len(words), date=today().isoformat(), rows=rows,
        scount=len(structs), srows=srows,
    )
    data = io.BytesIO(html_page.encode("utf-8"))
    data.name = f"mis_palabras_{lang}.html"
    await update.message.reply_document(
        data, caption=f"📖 Tus {len(words)} palabras y {len(structs)} estructuras. Ábrelo y se ve en el navegador, con buscador."
    )


@restricted
async def decir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Escribe la frase, por ejemplo:\n/decir 我已经学了两年中文")
        return
    try:
        await send_voice(update, context, text, user["language"])
    except Exception:
        log.exception("Error generando la voz")
        await update.message.reply_text("😕 No pude generar el audio.")


# ---------- repaso espaciado ----------

async def _send_next_card(chat_id: int, uid: int, lang: str, context) -> None:
    word = db.next_due(uid, lang, today())
    if not word:
        await context.bot.send_message(chat_id, "🎉 No tienes más repasos por hoy. ¡Buen trabajo!")
        return
    pending = db.count_due(uid, lang, today())
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👀 Mostrar respuesta", callback_data=f"show:{word['id']}")]])
    await context.bot.send_message(
        chat_id,
        f"<code>Pendientes: {pending}</code>\n\n<b>{e(word['word'])}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


@restricted
async def repasar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    await _send_next_card(update.effective_chat.id, user["id"], user["language"], context)


@restricted
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    words = [w for w in db.list_words(user["id"], user["language"], limit=300) if w["meaning"]]
    meanings = {w["meaning"] for w in words}
    if len(meanings) < 4:
        await update.message.reply_text("Necesitas al menos 4 palabras guardadas con significado para el quiz.")
        return
    target = random.choice(words)
    others = random.sample([m for m in meanings if m != target["meaning"]], 3)
    options = [target["meaning"], *others]
    random.shuffle(options)
    await update.message.reply_poll(
        question=f"¿Qué significa {target['word']}?"[:300],
        options=[o[:100] for o in options],
        type=Poll.QUIZ,
        correct_option_id=options.index(target["meaning"]),
        explanation=f"{target['word']} {target['reading']}\n{target['example']}"[:200],
        is_anonymous=False,
    )
    db.touch_study(user["id"], today())


# ---------- ejercicios ----------

@restricted
async def practicar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await practice.start_word(update.effective_chat.id, _user(update), context, today())


@restricted
async def describir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await practice.start_scene(update.effective_chat.id, _user(update), context)


@restricted
async def usar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await practice.start_structure(update.effective_chat.id, _user(update), context)


@restricted
async def pista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await practice.hint(update, context)


@restricted
async def terminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    had = context.user_data.pop("ex", None)
    await update.message.reply_text(
        "Listo, cerré el ejercicio. Ya podemos conversar normal 🙂" if had else "No había ningún ejercicio abierto."
    )


# ---------- botones ----------

@restricted
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = _user(update)
    uid = user["id"]
    action, _, value = query.data.partition(":")

    if action == "yt":
        await youtube.on_button(update, context, user, value, today())

    elif action == "ex":
        await query.edit_message_reply_markup(None)
        if value == "word":
            await practice.start_word(query.message.chat_id, user, context, today())
        elif value == "struct":
            await practice.start_structure(query.message.chat_id, user, context)
        elif value == "scene":
            await practice.start_scene(query.message.chat_id, user, context)
        else:
            context.user_data.pop("ex", None)
            await context.bot.send_message(query.message.chat_id, "👍 Buen trabajo. Seguimos conversando cuando quieras.")

    elif action == "lang":
        db.update_user(uid, language=value)
        user = db.get_user(uid)
        await query.edit_message_text(
            f"Ahora practicamos {LANG_LABEL[value]} (nivel {db.level_of(user)}). "
            "Puedes cambiar el nivel con /nivel."
        )

    elif action == "lvl":
        lang = user["language"]
        if value in LEVELS[lang]:
            db.update_user(uid, **{f"level_{lang}": value})
            await query.edit_message_text(f"Nivel actualizado: {value} ✅")

    elif action == "show":
        word = db.get_word(int(value), uid)
        if not word:
            return
        buttons = [[InlineKeyboardButton(label, callback_data=f"grade:{word['id']}:{g}") for g, label in srs.GRADES.items()]]
        await query.edit_message_text(
            word_card(word), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
        )
        if user["voice"]:
            try:
                path = await speech.synthesize(word["word"], word["language"])
                try:
                    with open(path, "rb") as f:
                        await context.bot.send_voice(query.message.chat_id, f)
                finally:
                    os.remove(path)
            except Exception:
                log.exception("Error generando la voz")

    elif action == "grade":
        word_id, _, grade = value.partition(":")
        word = db.get_word(int(word_id), uid)
        if not word:
            return
        ease, interval, reps, due = srs.schedule(
            word["ease"], word["interval"], word["reps"], int(grade), today()
        )
        db.update_srs(word["id"], ease, interval, reps, due)
        db.touch_study(uid, today())
        when = "hoy otra vez" if interval == 0 else f"en {interval} día{'s' if interval != 1 else ''}"
        await query.edit_message_text(
            word_card(word) + f"\n\n{srs.GRADES[int(grade)]} · vuelve {when}", parse_mode=ParseMode.HTML
        )
        await _send_next_card(query.message.chat_id, uid, word["language"], context)


# ---------- progreso y recordatorios ----------

@restricted
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    uid = user["id"]
    lines = [f"🔥 Racha: <b>{user['streak']}</b> día(s)", ""]
    for lang, label in LANG_LABEL.items():
        total = db.count_words(uid, lang)
        if not total and lang != user["language"]:
            continue
        lines.append(
            f"<b>{label}</b> ({user[f'level_{lang}']})\n"
            f"  📚 {total} palabras · ⏰ {db.count_due(uid, lang, today())} por repasar · "
            f"📌 {db.count_structures(uid, lang)} estructuras · "
            f"✏️ {db.count_mistakes(uid, lang)} correcciones"
        )
    if user["reminder"]:
        lines.append(f"\n🔔 Recordatorio diario a las {user['reminder']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.data
    user = db.get_user(uid)
    lang = user["language"]
    due = db.count_due(uid, lang, today())
    text = f"🔔 ¡Hora de estudiar {LANG_LABEL[lang]}!\n"
    text += f"Tienes {due} palabra(s) para /repasar." if due else "No tienes repasos pendientes, ¿practicamos conversación?"
    if user["streak"]:
        text += f"\n🔥 Racha actual: {user['streak']} día(s)"
    await context.bot.send_message(uid, text)


def schedule_reminder(job_queue, uid: int, hhmm: str | None) -> None:
    for job in job_queue.get_jobs_by_name(f"rem-{uid}"):
        job.schedule_removal()
    if hhmm:
        h, m = map(int, hhmm.split(":"))
        job_queue.run_daily(reminder_job, dtime(h, m, tzinfo=TZ), name=f"rem-{uid}", data=uid, chat_id=uid)


@restricted
async def recordatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _user(update)
    arg = context.args[0].lower() if context.args else ""
    if arg in ("off", "no", "quitar"):
        db.update_user(user["id"], reminder=None)
        schedule_reminder(context.job_queue, user["id"], None)
        await update.message.reply_text("🔕 Recordatorio desactivado.")
        return
    try:
        h, m = map(int, arg.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        await update.message.reply_text("Indica la hora así: /recordatorio 20:00  (o /recordatorio off)")
        return
    hhmm = f"{h:02d}:{m:02d}"
    db.update_user(user["id"], reminder=hhmm)
    schedule_reminder(context.job_queue, user["id"], hhmm)
    await update.message.reply_text(f"🔔 Te recordaré estudiar todos los días a las {hhmm}.")
