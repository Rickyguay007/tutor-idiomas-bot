"""Base de datos SQLite: usuarios, palabras, errores e historial de conversación."""
import os
import sqlite3
from datetime import date, datetime, timedelta

_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,          -- id de Telegram
    name        TEXT DEFAULT '',
    language    TEXT DEFAULT 'zh',            -- 'zh' o 'en'
    level_zh    TEXT DEFAULT 'HSK4',
    level_en    TEXT DEFAULT 'B1',
    voice       INTEGER DEFAULT 1,            -- 1 = responder también con nota de voz
    reminder    TEXT,                         -- 'HH:MM' o NULL
    streak      INTEGER DEFAULT 0,
    last_study  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS words (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    language            TEXT NOT NULL,
    word                TEXT NOT NULL,
    reading             TEXT DEFAULT '',      -- pinyin o pronunciación
    meaning             TEXT DEFAULT '',
    example             TEXT DEFAULT '',
    example_translation TEXT DEFAULT '',
    level               TEXT DEFAULT '',      -- HSK1..HSK6 / A1..C2
    source              TEXT DEFAULT '',      -- 'manual', 'conversación', 'YouTube'...
    created_at          TEXT,
    ease                REAL DEFAULT 2.5,     -- repaso espaciado
    interval            INTEGER DEFAULT 0,
    reps                INTEGER DEFAULT 0,
    due                 TEXT,
    UNIQUE (user_id, language, word)
);

CREATE TABLE IF NOT EXISTS mistakes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    language    TEXT NOT NULL,
    original    TEXT,
    corrected   TEXT,
    explanation TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    language    TEXT NOT NULL,
    role        TEXT NOT NULL,                -- 'user' o 'assistant'
    content     TEXT NOT NULL,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS structures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    language            TEXT NOT NULL,
    pattern             TEXT NOT NULL,        -- ej. 虽然…但是…
    explanation         TEXT DEFAULT '',
    example             TEXT DEFAULT '',
    example_reading     TEXT DEFAULT '',
    example_translation TEXT DEFAULT '',
    level               TEXT DEFAULT '',
    source              TEXT DEFAULT '',
    practiced           INTEGER DEFAULT 0,    -- veces que la usó en /usar
    last_practiced      TEXT,
    created_at          TEXT,
    UNIQUE (user_id, language, pattern)
);

CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    language    TEXT NOT NULL,
    video_id    TEXT NOT NULL,
    title       TEXT DEFAULT '',
    created_at  TEXT,
    UNIQUE (user_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_words_due ON words (user_id, language, due);
CREATE INDEX IF NOT EXISTS idx_history ON history (user_id, language, id);
"""

USER_FIELDS = {"name", "language", "level_zh", "level_en", "voice", "reminder"}
WORD_FIELDS = ("word", "reading", "meaning", "example", "example_translation", "level", "pos", "example_reading")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init(path: str) -> None:
    global _conn
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    # Bases creadas con versiones anteriores: agrega las columnas nuevas sin perder nada.
    for table, column in (("words", "video_id"), ("structures", "video_id"),
                          ("words", "pos"), ("words", "example_reading"), ("words", "mastered")):
        cols = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            kind = "INTEGER DEFAULT 0" if column == "mastered" else "TEXT DEFAULT ''"
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    _conn.commit()


# ---------- usuarios ----------

def get_user(uid: int, name: str = "") -> dict:
    row = _conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        _conn.execute(
            "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (uid, name, _now())
        )
        _conn.commit()
        row = _conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row)


def update_user(uid: int, **fields) -> None:
    bad = set(fields) - USER_FIELDS
    if bad:
        raise ValueError(f"Campos no permitidos: {bad}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    _conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), uid))
    _conn.commit()


def level_of(user: dict, lang: str | None = None) -> str:
    return user[f"level_{lang or user['language']}"]


def touch_study(uid: int, today: date) -> int:
    """Actualiza la racha de días seguidos y la devuelve."""
    user = get_user(uid)
    last = user["last_study"]
    if last == today.isoformat():
        return user["streak"]
    streak = user["streak"] + 1 if last == (today - timedelta(days=1)).isoformat() else 1
    _conn.execute(
        "UPDATE users SET streak = ?, last_study = ? WHERE id = ?",
        (streak, today.isoformat(), uid),
    )
    _conn.commit()
    return streak


def users_with_reminder() -> list[dict]:
    rows = _conn.execute("SELECT * FROM users WHERE reminder IS NOT NULL").fetchall()
    return [dict(r) for r in rows]


# ---------- palabras ----------

def add_word(uid: int, lang: str, today: date, source: str = "manual", video_id: str = "", **data) -> bool:
    """Guarda una palabra. Devuelve False si ya existía (si viene de un video, la vincula igual)."""
    word = (data.get("word") or "").strip()
    if not word:
        return False
    values = [(data.get(f) or "").strip() if isinstance(data.get(f), str) else "" for f in WORD_FIELDS]
    values[0] = word
    cur = _conn.execute(
        f"""INSERT OR IGNORE INTO words
            (user_id, language, {', '.join(WORD_FIELDS)}, source, created_at, due, video_id)
            VALUES (?, ?, {', '.join('?' * len(WORD_FIELDS))}, ?, ?, ?, ?)""",
        (uid, lang, *values, source, _now(), today.isoformat(), video_id),
    )
    if cur.rowcount == 0 and video_id:
        _conn.execute(
            "UPDATE words SET video_id = ? WHERE user_id = ? AND language = ? AND word = ? AND video_id = ''",
            (video_id, uid, lang, word),
        )
    _conn.commit()
    return cur.rowcount == 1


def set_mastered(word_id: int, uid: int, mastered: bool) -> None:
    """«Ya me la sé»: sale de repasos, quiz y ejercicios, pero sigue guardada."""
    _conn.execute("UPDATE words SET mastered = ? WHERE id = ? AND user_id = ?", (int(mastered), word_id, uid))
    _conn.commit()


def delete_word(word_id: int, uid: int) -> None:
    _conn.execute("DELETE FROM words WHERE id = ? AND user_id = ?", (word_id, uid))
    _conn.commit()


def count_mastered(uid: int, lang: str) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM words WHERE user_id = ? AND language = ? AND mastered = 1", (uid, lang)
    ).fetchone()[0]


def fill_word(word_id: int, pos: str, example_reading: str) -> None:
    """Completa tipo de palabra y pinyin del ejemplo en palabras guardadas antes de que existieran."""
    _conn.execute(
        "UPDATE words SET pos = CASE WHEN pos = '' THEN ? ELSE pos END, "
        "example_reading = CASE WHEN example_reading = '' THEN ? ELSE example_reading END WHERE id = ?",
        (pos.strip(), example_reading.strip(), word_id),
    )
    _conn.commit()


def get_word(word_id: int, uid: int) -> dict | None:
    row = _conn.execute(
        "SELECT * FROM words WHERE id = ? AND user_id = ?", (word_id, uid)
    ).fetchone()
    return dict(row) if row else None


def find_word(uid: int, lang: str, word: str) -> dict | None:
    row = _conn.execute(
        "SELECT * FROM words WHERE user_id = ? AND language = ? AND word = ?",
        (uid, lang, word.strip()),
    ).fetchone()
    return dict(row) if row else None


def list_words(uid: int, lang: str, limit: int = 20, level: str | None = None) -> list[dict]:
    sql = "SELECT * FROM words WHERE user_id = ? AND language = ? AND mastered = 0"
    params: list = [uid, lang]
    if level:
        sql += " AND level = ?"
        params.append(level)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def count_words(uid: int, lang: str) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM words WHERE user_id = ? AND language = ?", (uid, lang)
    ).fetchone()[0]


def next_due(uid: int, lang: str, today: date) -> dict | None:
    row = _conn.execute(
        """SELECT * FROM words WHERE user_id = ? AND language = ? AND due <= ? AND mastered = 0
           ORDER BY due, reps, id LIMIT 1""",
        (uid, lang, today.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def count_due(uid: int, lang: str, today: date) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM words WHERE user_id = ? AND language = ? AND due <= ? AND mastered = 0",
        (uid, lang, today.isoformat()),
    ).fetchone()[0]


def update_srs(word_id: int, ease: float, interval: int, reps: int, due: date) -> None:
    _conn.execute(
        "UPDATE words SET ease = ?, interval = ?, reps = ?, due = ? WHERE id = ?",
        (ease, interval, reps, due.isoformat(), word_id),
    )
    _conn.commit()


def all_words(uid: int, lang: str) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM words WHERE user_id = ? AND language = ? ORDER BY id",
        (uid, lang),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- estructuras gramaticales ----------

STRUCT_FIELDS = ("pattern", "explanation", "example", "example_reading", "example_translation", "level")


def _norm_pattern(p: str) -> str:
    # "虽然...但是..." y "虽然……但是……" cuentan como la misma estructura.
    return " ".join(p.replace("……", "…").replace("...", "…").split())


def add_structure(uid: int, lang: str, source: str = "conversación", video_id: str = "", **data) -> bool:
    """Guarda una estructura. Devuelve False si ya existía."""
    pattern = _norm_pattern(str(data.get("pattern") or ""))
    if not pattern:
        return False
    values = [str(data.get(f) or "").strip() for f in STRUCT_FIELDS]
    values[0] = pattern
    cur = _conn.execute(
        f"""INSERT OR IGNORE INTO structures
            (user_id, language, {', '.join(STRUCT_FIELDS)}, source, created_at, video_id)
            VALUES (?, ?, {', '.join('?' * len(STRUCT_FIELDS))}, ?, ?, ?)""",
        (uid, lang, *values, source, _now(), video_id),
    )
    _conn.commit()
    return cur.rowcount == 1


def find_structure(uid: int, lang: str, pattern: str) -> dict | None:
    row = _conn.execute(
        "SELECT * FROM structures WHERE user_id = ? AND language = ? AND pattern = ?",
        (uid, lang, _norm_pattern(pattern)),
    ).fetchone()
    return dict(row) if row else None


def list_structures(uid: int, lang: str, limit: int = 1000) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM structures WHERE user_id = ? AND language = ? ORDER BY id DESC LIMIT ?",
        (uid, lang, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def count_structures(uid: int, lang: str) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM structures WHERE user_id = ? AND language = ?", (uid, lang)
    ).fetchone()[0]


def structure_to_practice(uid: int, lang: str, exclude: int | None = None) -> dict | None:
    """La estructura menos practicada (y hace más tiempo), para /usar."""
    row = _conn.execute(
        """SELECT * FROM structures WHERE user_id = ? AND language = ? AND id != ?
           ORDER BY practiced, COALESCE(last_practiced, ''), RANDOM() LIMIT 1""",
        (uid, lang, exclude or -1),
    ).fetchone()
    return dict(row) if row else None


def mark_structure_practiced(sid: int) -> None:
    _conn.execute(
        "UPDATE structures SET practiced = practiced + 1, last_practiced = ? WHERE id = ?",
        (_now(), sid),
    )
    _conn.commit()


# ---------- canciones ----------

def add_song(uid: int, lang: str, video_id: str, title: str) -> int:
    _conn.execute(
        "INSERT OR IGNORE INTO songs (user_id, language, video_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, lang, video_id, title.strip() or "Canción sin nombre", _now()),
    )
    _conn.commit()
    return get_song_by_video(uid, video_id)["id"]


def get_song(song_id: int, uid: int) -> dict | None:
    row = _conn.execute("SELECT * FROM songs WHERE id = ? AND user_id = ?", (song_id, uid)).fetchone()
    return dict(row) if row else None


def get_song_by_video(uid: int, video_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM songs WHERE user_id = ? AND video_id = ?", (uid, video_id)).fetchone()
    return dict(row) if row else None


def list_songs(uid: int, lang: str) -> list[dict]:
    rows = _conn.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM words w WHERE w.user_id = s.user_id AND w.video_id = s.video_id) AS n_words,
                  (SELECT COUNT(*) FROM structures t WHERE t.user_id = s.user_id AND t.video_id = s.video_id) AS n_structs
           FROM songs s WHERE s.user_id = ? AND s.language = ? ORDER BY s.title COLLATE NOCASE""",
        (uid, lang),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_song(song_id: int, uid: int) -> None:
    """Quita la canción del catálogo; sus palabras siguen guardadas."""
    _conn.execute("DELETE FROM songs WHERE id = ? AND user_id = ?", (song_id, uid))
    _conn.commit()


def song_words(uid: int, video_id: str) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM words WHERE user_id = ? AND video_id = ? AND mastered = 0 ORDER BY due, id", (uid, video_id)
    ).fetchall()
    return [dict(r) for r in rows]


def song_structures(uid: int, video_id: str) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM structures WHERE user_id = ? AND video_id = ? ORDER BY id", (uid, video_id)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- errores ----------

def add_mistake(uid: int, lang: str, original: str, corrected: str, explanation: str) -> None:
    _conn.execute(
        """INSERT INTO mistakes (user_id, language, original, corrected, explanation, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, lang, original, corrected, explanation, _now()),
    )
    _conn.commit()


def recent_mistakes(uid: int, lang: str, limit: int = 5) -> list[dict]:
    rows = _conn.execute(
        """SELECT * FROM mistakes WHERE user_id = ? AND language = ?
           ORDER BY id DESC LIMIT ?""",
        (uid, lang, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def count_mistakes(uid: int, lang: str) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM mistakes WHERE user_id = ? AND language = ?", (uid, lang)
    ).fetchone()[0]


# ---------- historial de conversación ----------

def add_history(uid: int, lang: str, role: str, content: str) -> None:
    _conn.execute(
        "INSERT INTO history (user_id, language, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, lang, role, content, _now()),
    )
    _conn.commit()


def get_history(uid: int, lang: str, limit: int) -> list[dict]:
    rows = _conn.execute(
        """SELECT role, content FROM history WHERE user_id = ? AND language = ?
           ORDER BY id DESC LIMIT ?""",
        (uid, lang, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_history(uid: int, lang: str) -> None:
    _conn.execute("DELETE FROM history WHERE user_id = ? AND language = ?", (uid, lang))
    _conn.commit()
