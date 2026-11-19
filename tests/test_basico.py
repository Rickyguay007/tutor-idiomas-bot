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
