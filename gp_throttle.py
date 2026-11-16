"""Бережный режим автопоиска Goofish: случайный разброс пауз и нарастающая пауза после ограничения сайта
(RGV587, «被挤爆», проверка). Никакого обхода проверок и маскировки: цель — чтобы запросов было меньше и шли они реже.
Чистая логика без окна и сети — проверяется в tests/test_throttle.py."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

SAME_INCIDENT_SEC = 600     # два сигнала ограничения за 10 минут — один случай (сайт и адрес страницы сообщают о нём оба)
DAY = 86400


def jittered(pause: float, jitter: float, rnd=random.random) -> float:
    """Пауза со случайной прибавкой от 0 до jitter (доля): меньше базовой пауза не бывает, запросы не идут «по метроному»."""
    return pause * (1 + max(jitter, 0.0) * rnd())


def backoff_minutes(n: int, base: float, factor: float, cap: float) -> float:
    """Пауза после n-го ограничения за сутки: base, base·factor, base·factor² … но не больше cap (минут)."""
    return min(base * max(factor, 1.0) ** max(n - 1, 0), cap)


class BlockLog:
    """Когда сайт сообщал об ограничении (хранится в файле, чтобы перезапуск приложения не обнулял счёт)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.ts = [float(x) for x in json.loads(self.path.read_text(encoding="utf-8"))]
        except Exception:
            self.ts = []

    def count(self, now: float | None = None) -> int:
        now = now or time.time()
        return sum(1 for t in self.ts if now - t < DAY)

    def add(self, now: float | None = None) -> int:
        """Записывает ограничение и возвращает, какое оно по счёту за сутки. Повторный сигнал о том же случае не считается."""
        now = now or time.time()
        self.ts = [t for t in self.ts if now - t < DAY]
        if not self.ts or now - self.ts[-1] >= SAME_INCIDENT_SEC:
            self.ts.append(now)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.ts), encoding="utf-8")
            except OSError:
                pass
        return len(self.ts)

    def plan(self, now: float, base: float, factor: float, cap: float, max_per_day: int) -> dict:
        """Что делать после ограничения: {"n": какое по счёту, "wait_min": пауза, "resume_at": время, "halt": стоп до команды}."""
        n = self.add(now)
        wait = backoff_minutes(n, base, factor, cap)
        return {"n": n, "wait_min": wait, "resume_at": now + wait * 60, "halt": n >= max_per_day}
