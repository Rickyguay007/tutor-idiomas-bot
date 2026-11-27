"""Ejercicios guiados: traducir una palabra y describir una imagen.

/practicar  → el bot da una palabra en español; el estudiante la dice en el idioma
              y arma una oración con ella.
/describir  → el bot manda una escena dibujada; el estudiante describe en el idioma
              qué está pasando.

Mientras hay un ejercicio abierto, lo que el estudiante escriba o diga por voz se
toma como respuesta. /terminar lo cierra.
"""
import io
import logging
import random
import urllib.parse

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from . import ai, db, srs
from .prompts import LANG_NAMES

log = logging.getLogger(__name__)

IMAGE_URL = "https://image.pollinations.ai/prompt/{prompt}?width=768&height=768&nologo=true&safe=true&seed={seed}"


def _e(text) -> str:
    import html
    return html.escape(str(text or ""))


def _reading_rule(lang: str) -> str:
    return "pinyin con marcas de tono" if lang == "zh" else "transcripción IPA"


# ---------- instrucciones para la IA ----------

def pick_word_system(lang: str, level: str) -> str:
    return f"""Eres profesora de {LANG_NAMES[lang]} para hispanohablantes de nivel {level}.
Elige UNA palabra útil de ese nivel (sustantivo, verbo o adjetivo frecuente) para practicar traducción.
Devuelve SOLO un objeto JSON:
{{"word": "la palabra en {LANG_NAMES[lang]}", "reading": "{_reading_rule(lang)}", "meaning": "significado en español, 1 a 3 palabras", "level": "{level}"}}"""


def eval_word_system(lang: str, level: str) -> str:
    return f"""Eres profesora de {LANG_NAMES[lang]} para un hispanohablante de nivel {level}.
Le pediste traducir una palabra del español y hacer una oración con ella. Evalúa su respuesta con cariño pero con rigor.
Acepta sinónimos correctos. La respuesta puede venir de una nota de voz transcrita: no castigues errores de transcripción obvios.
Devuelve SOLO un objeto JSON:
{{
  "word_ok": true o false (¿dijo bien la palabra o un sinónimo válido?),
  "sentence_ok": true o false (¿la oración es correcta y usa la palabra?),
  "feedback": "comentario breve en español, 1 o 2 frases",
  "corrected": "su oración corregida (vacío si ya estaba bien o si no hizo oración)",
  "corrected_reading": "{_reading_rule(lang)} de corrected",
  "example": "otra oración modelo natural con la palabra, de su nivel",
  "example_reading": "{_reading_rule(lang)} de example",
  "example_translation": "traducción al español de example"
}}"""


def eval_structure_system(lang: str, level: str, st: dict) -> str:
    return f"""Eres profesora de {LANG_NAMES[lang]} para un hispanohablante de nivel {level}.
Le pediste hacer una oración propia usando la estructura «{st.get('pattern')}» ({st.get('explanation', '')}).
Evalúa si la usó bien. La respuesta puede venir de una nota de voz transcrita: no castigues errores de transcripción obvios.
Devuelve SOLO un objeto JSON:
{{
  "ok": true o false (¿usó la estructura de forma correcta y natural?),
  "feedback": "comentario breve en español, 1 o 2 frases",
  "corrected": "su oración corregida (vacío si ya estaba bien)",
  "corrected_reading": "{_reading_rule(lang)} de corrected",
  "example": "otra oración modelo con la estructura, distinta a la suya",
  "example_reading": "{_reading_rule(lang)} de example",
  "example_translation": "traducción al español de example"
}}"""


def scene_system(lang: str, level: str) -> str:
    return f"""Eres profesora de {LANG_NAMES[lang]} para hispanohablantes de nivel {level}.
Inventa una escena cotidiana y concreta para que el estudiante la describa (personas haciendo algo: en un mercado, cocinando, en el parque, en una oficina, viajando, etc.). Varía mucho las escenas.
Devuelve SOLO un objeto JSON:
{{
  "image_prompt": "descripción en INGLÉS para generar la ilustración: estilo dibujo sencillo y colorido, sin texto ni letras, 1 a 3 personas, acciones claras y visibles",
  "scene_es": "lo que se ve, en español, 1 o 2 frases",
  "key_words": [{{"word": "", "reading": "{_reading_rule(lang)}", "meaning": "significado en español"}}]
}}
En key_words pon 3 o 4 palabras de su nivel útiles para describir la escena."""


def eval_scene_system(lang: str, level: str, scene: dict) -> str:
    words = ", ".join(w.get("word", "") for w in scene.get("key_words", []))
    return f"""Eres profesora de {LANG_NAMES[lang]} para un hispanohablante de nivel {level}.
Le mostraste una ilustración de esta escena: {scene.get('scene_es', '')}
Palabras útiles de la escena: {words}
Él la describió en {LANG_NAMES[lang]}. Evalúa si su descripción corresponde a la escena y corrige sus errores.
La respuesta puede venir de una nota de voz transcrita: no castigues errores de transcripción obvios.
Devuelve SOLO un objeto JSON:
{{
  "feedback": "comentario breve en español: qué hizo bien y qué le faltó describir",
  "corrections": [{{"original": "", "original_reading": "", "corrected": "", "corrected_reading": "", "explanation": "por qué, en español, breve"}}],
  "model": "una descripción modelo de 2 o 3 oraciones, de su nivel",
  "model_reading": "{_reading_rule(lang)} de model",
  "model_translation": "traducción al español de model"
}}
En las correcciones usa {_reading_rule(lang)} en los campos *_reading."""


# ---------- utilidades ----------

def _buttons(kind: str) -> InlineKeyboardMarkup:
    label = {"word": "➡️ Otra palabra", "struct": "➡️ Otra estructura"}.get(kind, "🖼️ Otra imagen")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"ex:{kind}"),
        InlineKeyboardButton("⏹ Terminar", callback_data="ex:stop"),
    ]])


async def _fetch_image(prompt: str) -> bytes | None:
    url = IMAGE_URL.format(prompt=urllib.parse.quote(prompt), seed=random.randint(1, 10**6))
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            r = await client.get(url)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
        log.warning("Imagen no disponible: HTTP %s", r.status_code)
    except Exception:
        log.exception("Error descargando la imagen")
    return None


# ---------- iniciar ejercicios ----------

async def start_word(chat_id: int, user: dict, context: ContextTypes.DEFAULT_TYPE, today, pool: list | None = None) -> None:
    lang = user["language"]
    level = db.level_of(user)
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    # Primero las palabras que ya guardó (así repasa lo suyo); si tiene pocas, la IA propone una.
    # Si viene de una canción, solo palabras de esa canción.
    saved = pool if pool is not None else [w for w in db.list_words(user["id"], lang, limit=300) if w["meaning"]]
    last = context.user_data.get("last_word")
    choices = [w for w in saved if w["word"] != last] or saved
    if pool is not None or (len(choices) >= 5 and random.random() < 0.8):
        word = random.choice(choices)
    else:
        try:
            word = await ai.ask_json(pick_word_system(lang, level), "Dame una palabra.", temperature=1.0)
        except Exception:
            log.exception("Error eligiendo palabra")
            await context.bot.send_message(chat_id, "😕 No pude conectar con la IA. Intenta de nuevo.")
            return
        word["id"] = None

    context.user_data["ex"] = {"kind": "word", "word": word, "lang": lang}
    context.user_data["last_word"] = word.get("word")
    target = "chino" if lang == "zh" else "inglés"
    await context.bot.send_message(
        chat_id,
        f"🔤 ¿Cómo se dice <b>«{_e(word.get('meaning'))}»</b> en {target}?\n\n"
        "Dime la palabra y luego haz una oración con ella. Puedes escribir o mandar nota de voz.\n"
        "<i>/pista si te atascas · /terminar para salir</i>",
        parse_mode=ParseMode.HTML,
    )


async def start_structure(chat_id: int, user: dict, context: ContextTypes.DEFAULT_TYPE, pool: list | None = None) -> None:
    lang = user["language"]
    last = context.user_data.get("last_struct")
    if pool:
        choices = [p for p in pool if p["id"] != last] or pool
        st = min(choices, key=lambda p: (p["practiced"], p["last_practiced"] or ""))
    else:
        st = db.structure_to_practice(user["id"], lang, exclude=last)
    if not st:
        await context.bot.send_message(
            chat_id,
            "Todavía no tienes estructuras guardadas. Conversa conmigo y se irán guardando, "
            "o pídeme «guárdame la estructura 虽然…但是…».",
        )
        return
    context.user_data["ex"] = {"kind": "struct", "struct": st, "lang": lang}
    context.user_data["last_struct"] = st["id"]
    await context.bot.send_message(
        chat_id,
        f"📌 Haz una oración tuya con:\n\n<b>{_e(st['pattern'])}</b>\n{_e(st['explanation'])}\n\n"
        "Puedes escribir o mandar nota de voz.\n<i>/pista para ver un ejemplo · /terminar para salir</i>",
        parse_mode=ParseMode.HTML,
    )


async def start_scene(chat_id: int, user: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = user["language"]
    level = db.level_of(user)
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        scene = await ai.ask_json(scene_system(lang, level), "Nueva escena, distinta a las anteriores.", temperature=1.0)
    except Exception:
        log.exception("Error creando escena")
        await context.bot.send_message(chat_id, "😕 No pude conectar con la IA. Intenta de nuevo.")
        return

    context.user_data["ex"] = {"kind": "scene", "scene": scene}
    target = "chino" if lang == "zh" else "inglés"
    instructions = (
        f"🖼️ Describe en {target} qué está pasando: quiénes están, dónde y qué hacen.\n"
        "<i>/pista para ver palabras útiles · /terminar para salir</i>"
    )
    image = await _fetch_image(scene.get("image_prompt") or scene.get("scene_es", ""))
    if image:
        photo = io.BytesIO(image)
        photo.name = "escena.jpg"
        await context.bot.send_photo(chat_id, photo, caption=instructions, parse_mode=ParseMode.HTML)
    else:
        # Si el servicio de imágenes falla o está ocupado, se practica igual con la escena en texto.
        await context.bot.send_message(
            chat_id,
            f"(No pude dibujar la imagen ahora, imagínala 🙂)\n\n<b>{_e(scene.get('scene_es'))}</b>\n\n{instructions}",
            parse_mode=ParseMode.HTML,
        )


# ---------- pistas y respuestas ----------

async def hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ex = context.user_data.get("ex")
    if not ex:
        await update.message.reply_text("No hay ningún ejercicio abierto. Prueba /practicar o /describir.")
        return
    if ex["kind"] == "struct":
        st = ex["struct"]
        text = f"💡 Ejemplo: {st['example']}" if st.get("example") else "💡 No tengo un ejemplo guardado para esta."
        if st.get("example_reading"):
            text += f"\n{st['example_reading']}"
        await update.message.reply_text(text)
        return
    if ex["kind"] == "word":
        w = ex["word"]
        word = str(w.get("word", "")).strip()
        reading = str(w.get("reading", "")).strip()
        first = (reading or word)[:1]
        size = f"{len(word)} carácter(es)" if ex.get("lang", "zh") == "zh" else f"{len(word)} letras"
        await update.message.reply_text(f"💡 Tiene {size} y su pronunciación empieza con «{first}».")
    else:
        words = ex["scene"].get("key_words", [])
        lines = [f"• {_e(w.get('word'))} <i>{_e(w.get('reading'))}</i> – {_e(w.get('meaning'))}" for w in words]
        await update.message.reply_text("💡 Palabras útiles:\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict, text: str, today) -> None:
    """Evalúa la respuesta del estudiante al ejercicio abierto."""
    ex = context.user_data.get("ex")
    lang = user["language"]
    level = db.level_of(user)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    if ex["kind"] == "word":
        w = ex["word"]
        system = eval_word_system(lang, level)
        prompt = (
            f"Palabra pedida: «{w.get('meaning')}» (respuesta esperada: {w.get('word')} {w.get('reading', '')}).\n"
            f"Respuesta del estudiante: {text}"
        )
    elif ex["kind"] == "struct":
        st = ex["struct"]
        system = eval_structure_system(lang, level, st)
        prompt = f"Oración del estudiante: {text}"
    else:
        system = eval_scene_system(lang, level, ex["scene"])
        prompt = f"Descripción del estudiante: {text}"

    try:
        data = await ai.ask_json(system, prompt, temperature=0.3)
    except Exception:
        log.exception("Error evaluando")
        await update.effective_message.reply_text("😕 No pude conectar con la IA. Intenta responder otra vez.")
        return

    db.touch_study(user["id"], today)
    parts = []
    if ex["kind"] == "word":
        ok = bool(data.get("word_ok"))
        icon = "✅" if ok and data.get("sentence_ok") else ("🟡" if ok else "❌")
        parts.append(f"{icon} {_e(data.get('feedback'))}")
        parts.append(f"\n<b>{_e(w.get('word'))}</b> <i>{_e(w.get('reading'))}</i> – {_e(w.get('meaning'))}")
        if data.get("corrected"):
            parts.append(f"\n✏️ <b>{_e(data['corrected'])}</b>\n<i>{_e(data.get('corrected_reading'))}</i>")
            db.add_mistake(user["id"], lang, text, data["corrected"], data.get("feedback", ""))
        if data.get("example"):
            parts.append(
                f"\n📝 {_e(data['example'])}\n<i>{_e(data.get('example_reading'))}</i>\n"
                f"🇪🇸 {_e(data.get('example_translation'))}"
            )
        # Si era una palabra guardada, la respuesta cuenta como repaso.
        saved = db.find_word(user["id"], lang, str(w.get("word", "")))
        if saved:
            grade = 2 if ok else 0
            db.update_srs(saved["id"], *srs.schedule(saved["ease"], saved["interval"], saved["reps"], grade, today))
        elif not ok:
            db.add_word(user["id"], lang, today, source="práctica", **{k: w.get(k, "") for k in ("word", "reading", "meaning", "level")})
            parts.append("\n🆕 La guardé en tus palabras para repasarla.")
    elif ex["kind"] == "struct":
        parts.append(f"{'✅' if data.get('ok') else '🟡'} {_e(data.get('feedback'))}")
        parts.append(f"\n📌 <b>{_e(st['pattern'])}</b>")
        if data.get("corrected"):
            parts.append(f"\n✏️ <b>{_e(data['corrected'])}</b>\n<i>{_e(data.get('corrected_reading'))}</i>")
            db.add_mistake(user["id"], lang, text, data["corrected"], data.get("feedback", ""))
        if data.get("example"):
            parts.append(
                f"\n📝 {_e(data['example'])}\n<i>{_e(data.get('example_reading'))}</i>\n"
                f"🇪🇸 {_e(data.get('example_translation'))}"
            )
        db.mark_structure_practiced(st["id"])
    else:
        parts.append(f"💬 {_e(data.get('feedback'))}")
        corrections = [c for c in data.get("corrections") or [] if isinstance(c, dict) and c.get("corrected")]
        if corrections:
            parts.append("\n✏️ <b>Correcciones</b>")
            for c in corrections:
                db.add_mistake(user["id"], lang, c.get("original", ""), c["corrected"], c.get("explanation", ""))
                line = f"• <s>{_e(c.get('original'))}</s>"
                if c.get("original_reading"):
                    line += f" <i>({_e(c['original_reading'])})</i>"
                line += f"\n  → <b>{_e(c['corrected'])}</b>"
                if c.get("corrected_reading"):
                    line += f" <i>({_e(c['corrected_reading'])})</i>"
                if c.get("explanation"):
                    line += f"\n  {_e(c['explanation'])}"
                parts.append(line)
        if data.get("model"):
            parts.append(
                f"\n🌟 <b>Así lo podrías decir</b>\n{_e(data['model'])}\n<i>{_e(data.get('model_reading'))}</i>\n"
                f"🇪🇸 {_e(data.get('model_translation'))}"
            )

    message = "\n".join(parts)[:4000]
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML, reply_markup=_buttons(ex["kind"]))
    context.user_data["ex_done"] = ex
    context.user_data.pop("ex", None)

    # Respuesta modelo en audio, para escuchar la pronunciación.
    spoken = data.get("model") if ex["kind"] == "scene" else data.get("example")
    if user["voice"] and spoken:
        from .handlers import send_voice
        try:
            await send_voice(update, context, spoken, lang)
        except Exception:
            log.exception("Error generando la voz")
