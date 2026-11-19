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

CREATE INDEX IF NOT EXISTS idx_words_due ON words (user_id, language, due);
CREATE INDEX IF NOT EXISTS idx_history ON history (user_id, language, id);
"""

USER_FIELDS = {"name", "language", "level_zh", "level_en", "voice", "reminder"}
WORD_FIELDS = ("word", "reading", "meaning", "example", "example_translation", "level")


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

def add_word(uid: int, lang: str, today: date, source: str = "manual", **data) -> bool:
    """Guarda una palabra. Devuelve False si ya existía."""
    word = (data.get("word") or "").strip()
    if not word:
        return False
    values = [(data.get(f) or "").strip() if isinstance(data.get(f), str) else "" for f in WORD_FIELDS]
    values[0] = word
    cur = _conn.execute(
        f"""INSERT OR IGNORE INTO words
            (user_id, language, {', '.join(WORD_FIELDS)}, source, created_at, due)
            VALUES (?, ?, {', '.join('?' * len(WORD_FIELDS))}, ?, ?, ?)""",
        (uid, lang, *values, source, _now(), today.isoformat()),
    )
    _conn.commit()
    return cur.rowcount == 1


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
    sql = "SELECT * FROM words WHERE user_id = ? AND language = ?"
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
        """SELECT * FROM words WHERE user_id = ? AND language = ? AND due <= ?
           ORDER BY due, reps, id LIMIT 1""",
        (uid, lang, today.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def count_due(uid: int, lang: str, today: date) -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM words WHERE user_id = ? AND language = ? AND due <= ?",
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
