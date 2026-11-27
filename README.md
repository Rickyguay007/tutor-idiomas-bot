# Tutor de idiomas para Telegram 🇨🇳🇬🇧

Bot de código abierto para practicar **chino mandarín** e **inglés** desde el celular. Conversa contigo por texto o notas de voz, te corrige, guarda el vocabulario que vas aprendiendo y te lo hace repasar con tarjetas de repaso espaciado, como Anki.

## Qué hace

- **Conversación con una tutora IA**, adaptada a tu nivel (HSK1–HSK6 o A1–C2). Si le escribes en español, te explica gramática o te ayuda a decir algo.
- **Notas de voz** en ambos sentidos. Tu audio se transcribe con Whisper y la respuesta llega como nota de voz (Edge TTS, gratis).
- **Correcciones** de tus frases, que se guardan y la tutora vuelve a reforzar más adelante.
- **Vocabulario automático**. Las palabras nuevas de la conversación se guardan solas, y con `/agregar 坚持` guardas las que encuentres en YouTube o en clase, con pinyin, significado y ejemplo.
- **Repaso espaciado** (`/repasar`), **quiz** con encuestas de Telegram (`/quiz`) y **pronunciación** de cualquier frase (`/decir`).
- **Progreso**: racha de días, palabras, repasos pendientes y recordatorio diario.
- **Exportar a Anki** en CSV.
- Varios usuarios en el mismo bot, cada uno con su propio progreso.

## Instalación

Necesitas Python 3.11 o superior y `ffmpeg`, o solo Docker.

1. Crea el bot con [@BotFather](https://t.me/BotFather) (`/newbot`) y copia el token.
2. Consigue una clave de IA. DeepSeek es muy barato, Gemini tiene plan gratuito y también sirve cualquier API compatible con OpenAI (ver `.env.example`).
3. Para notas de voz, crea una clave gratuita en [Groq](https://console.groq.com).
4. Clona y configura:

```bash
git clone https://github.com/TU_USUARIO/tutor-idiomas-bot.git
cd tutor-idiomas-bot
cp .env.example .env      # y llena TELEGRAM_TOKEN, LLM_API_KEY, STT_API_KEY
```

### Opción A: con Docker (recomendado para servidor)

```bash
docker compose up -d --build
docker compose logs -f      # ver qué está pasando
```

### Opción B: con Python

```bash
sudo apt install ffmpeg          # Linux Mint / Ubuntu
python3 -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

## Actualizar

Tus datos viven en `data/` y tu configuración en `.env`, que Git no toca. Para recibir mejoras:

```bash
git pull
docker compose up -d --build    # o reinicia python -m bot.main
```

## Comandos

| Comando | Para qué |
|---|---|
| `/idioma` | Cambiar entre chino e inglés |
| `/nivel` | Elegir HSK1–6 o A1–C2 |
| `/voz` | Activar o quitar las respuestas en audio |
| `/agregar 坚持` | Guardar una palabra |
| `/palabras [HSK4]` | Ver las últimas palabras (con filtro de nivel opcional) |
| `/buscar 坚持` | Ver una palabra guardada |
| `/repasar` | Tarjetas con repaso espaciado |
| `/quiz` | Pregunta de opción múltiple con tus palabras |
| `/decir 你好` | Escuchar la pronunciación |
| `/stats` | Racha y progreso |
| `/recordatorio 20:00` | Aviso diario (`off` para quitarlo) |
| `/exportar` | CSV para Anki |
| `/practicar`, `/describir`, `/usar` | Ejercicios de traducción, imagen y estructuras |
| `/estructuras`, `/ver` | Ver tus estructuras y todo tu vocabulario |
| Enlace de YouTube | Propone palabras y estructuras del video |
| `/canciones` | Catálogo de canciones: repasar, quiz y ejercicios por canción |
| `/olvidar` | Reiniciar la conversación |

## Estructura

```
bot/
├── main.py       arranque y registro de comandos
├── handlers.py   lo que hace cada comando y mensaje
├── ai.py         conexión con la IA
├── prompts.py    instrucciones de la profesora (aquí se ajusta su método)
├── speech.py     voz a texto y texto a voz
├── srs.py        algoritmo de repaso espaciado
├── db.py         base de datos SQLite
└── config.py     lectura del .env
```

## Próximas ideas

- Mini App de Telegram (React) para ver el vocabulario, filtrar por nivel y repasar con tarjetas.
- Puntuación de pronunciación y tonos.
- Lecciones guiadas por estructura gramatical y juegos de rol.

## Licencia

MIT. Puedes usarlo, modificarlo y compartirlo libremente.
