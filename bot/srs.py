"""Repaso espaciado al estilo Anki (versión simplificada de SM-2)."""
from datetime import date, timedelta

GRADES = {0: "🔁 Otra vez", 1: "😓 Difícil", 2: "🙂 Bien", 3: "😎 Fácil"}


def schedule(ease: float, interval: int, reps: int, grade: int, today: date):
    """Devuelve (ease, interval, reps, due) después de calificar una tarjeta."""
    if grade == 0:  # no la recordó: vuelve hoy y se reinicia
        return round(max(1.3, ease - 0.2), 2), 0, 0, today

    if grade == 1:  # difícil
        interval = 1 if reps == 0 else max(interval + 1, round(interval * 1.2))
        ease = max(1.3, ease - 0.15)
    elif grade == 2:  # bien
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 3
        else:
            interval = max(interval + 1, round(interval * ease))
    else:  # fácil
        if reps == 0:
            interval = 3
        elif reps == 1:
            interval = 6
        else:
            interval = max(interval + 1, round(interval * ease * 1.3))
        ease += 0.15

    return round(ease, 2), interval, reps + 1, today + timedelta(days=interval)
