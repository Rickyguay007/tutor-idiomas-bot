"""Pruebas sin conexión a internet: python -m pytest"""
from datetime import date, timedelta

from bot import db, srs

HOY = date(2026, 11, 18)


def test_srs_intervalos_crecen():
    ease, interval, reps, due = 2.5, 0, 0, HOY
    vistos = []
    for _ in range(4):
        ease, interval, reps, due = srs.schedule(ease, interval, reps, 2, HOY)
        vistos.append(interval)
    assert vistos[0] == 1 and vistos[1] == 3
    assert vistos == sorted(vistos) and vistos[-1] > vistos[-2]


def test_srs_olvido_reinicia():
    ease, interval, reps, due = srs.schedule(2.5, 10, 4, 0, HOY)
    assert (interval, reps, due) == (0, 0, HOY)
    assert ease < 2.5


def test_base_de_datos(tmp_path):
    db.init(str(tmp_path / "t.db"))
    u = db.get_user(1, "Ricardo")
    assert u["language"] == "zh" and db.level_of(u) == "HSK4"

    assert db.add_word(1, "zh", HOY, word="坚持", reading="jiānchí", meaning="persistir", level="HSK4")
    assert not db.add_word(1, "zh", HOY, word="坚持")          # no se duplica
    assert db.add_word(1, "en", HOY, word="persist")           # otro idioma, aparte
    assert db.count_words(1, "zh") == 1

    w = db.next_due(1, "zh", HOY)
    assert w["word"] == "坚持"
    ease, interval, reps, due = srs.schedule(w["ease"], w["interval"], w["reps"], 2, HOY)
    db.update_srs(w["id"], ease, interval, reps, due)
    assert db.next_due(1, "zh", HOY) is None
    assert db.count_due(1, "zh", HOY + timedelta(days=1)) == 1

    assert db.touch_study(1, HOY) == 1
    assert db.touch_study(1, HOY + timedelta(days=1)) == 2
    assert db.touch_study(1, HOY + timedelta(days=3)) == 1

    for i in range(12):
        db.add_history(1, "zh", "user", f"m{i}")
    hist = db.get_history(1, "zh", 4)
    assert [h["content"] for h in hist] == ["m8", "m9", "m10", "m11"]


def test_estructuras(tmp_path):
    db.init(str(tmp_path / "s.db"))
    db.get_user(1)
    assert db.add_structure(1, "zh", pattern="虽然...但是...", explanation="aunque… pero…", level="HSK4")
    assert not db.add_structure(1, "zh", pattern="虽然……但是……")   # misma estructura
    assert db.add_structure(1, "zh", pattern="把 + objeto + verbo")
    assert db.count_structures(1, "zh") == 2
    st = db.structure_to_practice(1, "zh")
    db.mark_structure_practiced(st["id"])
    otra = db.structure_to_practice(1, "zh")
    assert otra["id"] != st["id"]                                # primero la menos practicada


def test_instrucciones_se_arman():
    from bot import prompts
    for lang, level in (("zh", "HSK4"), ("en", "B1")):
        texto = prompts.tutor_system(lang, level, "Ricardo", [{"original": "a", "corrected": "b"}])
        assert "save_structures" in texto and "save_words" in texto


def test_enlaces_youtube():
    from bot.youtube import video_id
    assert video_id("mira https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
    assert video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("hola 你好") is None


def test_minuto_del_enlace():
    from bot.youtube import start_seconds
    assert start_seconds("https://youtu.be/dQw4w9WgXcQ?t=754") == 754
    assert start_seconds("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12m34s") == 754
    assert start_seconds("https://youtu.be/dQw4w9WgXcQ") == 0


def test_canciones(tmp_path):
    import sqlite3
    viejo = tmp_path / "viejo.db"                      # base de una versión anterior, sin video_id
    c = sqlite3.connect(viejo)
    c.executescript("CREATE TABLE words (id INTEGER PRIMARY KEY, user_id INT, language TEXT, word TEXT, reading TEXT DEFAULT '', meaning TEXT DEFAULT '', example TEXT DEFAULT '', example_translation TEXT DEFAULT '', level TEXT DEFAULT '', source TEXT DEFAULT '', created_at TEXT, ease REAL DEFAULT 2.5, interval INTEGER DEFAULT 0, reps INTEGER DEFAULT 0, due TEXT, UNIQUE(user_id, language, word));"
                    "INSERT INTO words (user_id, language, word, due) VALUES (1, 'zh', '月亮', '2026-11-18');")
    c.commit(); c.close()
    db.init(str(viejo))
    db.get_user(1)
    assert db.count_words(1, "zh") == 1                # no se pierde nada
    sid = db.add_song(1, "zh", "abcdefghijk", "月亮代表我的心")
    db.add_word(1, "zh", HOY, video_id="abcdefghijk", word="代表", meaning="representar")
    db.add_word(1, "zh", HOY, video_id="abcdefghijk", word="月亮")   # ya existía: se vincula
    assert {w["word"] for w in db.song_words(1, "abcdefghijk")} == {"代表", "月亮"}
    assert db.list_songs(1, "zh")[0]["n_words"] == 2
    db.delete_song(sid, 1)
    assert db.list_songs(1, "zh") == [] and db.count_words(1, "zh") == 2


def test_tipo_y_pinyin_del_ejemplo(tmp_path):
    db.init(str(tmp_path / "p.db"))
    db.get_user(1)
    db.add_word(1, "zh", HOY, word="自然", reading="zìrán", pos="adjetivo", meaning="natural",
                example="你会听到自然的中文", example_reading="nǐ huì tīngdào zìrán de zhōngwén")
    w = db.find_word(1, "zh", "自然")
    assert w["pos"] == "adjetivo" and w["example_reading"].startswith("nǐ")
    db.add_word(1, "zh", HOY, word="书")
    db.fill_word(db.find_word(1, "zh", "书")["id"], "sustantivo", "")
    assert db.find_word(1, "zh", "书")["pos"] == "sustantivo"
