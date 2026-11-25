"""Punto de entrada: python -m bot.main"""
import logging

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from . import config, db, handlers

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

COMMANDS = [
    ("start", "Inicio y ayuda", handlers.start),
    ("ayuda", "Ver todos los comandos", handlers.help_cmd),
    ("idioma", "Cambiar entre chino e inglés", handlers.idioma),
    ("nivel", "Cambiar tu nivel", handlers.nivel),
    ("voz", "Activar o quitar respuestas en audio", handlers.voz),
    ("agregar", "Guardar una palabra", handlers.agregar),
    ("ver", "Ver todas tus palabras (página)", handlers.ver),
    ("palabras", "Ver tus últimas palabras", handlers.palabras),
    ("estructuras", "Ver tus estructuras gramaticales", handlers.estructuras),
    ("buscar", "Ver una palabra guardada", handlers.buscar),
    ("practicar", "Traducir una palabra y hacer una oración", handlers.practicar),
    ("usar", "Practicar una estructura gramatical", handlers.usar),
    ("describir", "Describir una imagen", handlers.describir),
    ("pista", "Ayuda en el ejercicio", handlers.pista),
    ("terminar", "Salir del ejercicio", handlers.terminar),
    ("repasar", "Repaso con tarjetas", handlers.repasar),
    ("quiz", "Pregunta rápida", handlers.quiz),
    ("decir", "Escuchar una frase", handlers.decir),
    ("stats", "Tu progreso", handlers.stats),
    ("recordatorio", "Aviso diario para estudiar", handlers.recordatorio),
    ("exportar", "Descargar para Anki", handlers.exportar),
    ("olvidar", "Reiniciar la conversación", handlers.olvidar),
]


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(name, desc) for name, desc, _ in COMMANDS])
    for user in db.users_with_reminder():
        handlers.schedule_reminder(app.job_queue, user["id"], user["reminder"])


def main() -> None:
    db.init(config.DB_PATH)
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()

    for name, _, func in COMMANDS:
        app.add_handler(CommandHandler(name, func))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CallbackQueryHandler(handlers.on_button))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handlers.on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    logging.info("Bot iniciado. Ctrl+C para detenerlo.")
    app.run_polling(allowed_updates=["message", "callback_query", "poll_answer"])


if __name__ == "__main__":
    main()
