# -*- coding: utf-8 -*-
"""ЧАСТЬ 4 этапа CLIENT: почему автоочистка не убрала накопившиеся сессии.

ТОЛЬКО ЧТЕНИЕ. Ничего не удаляет и не переписывает: перечисляет `.enc`
хранилища, расшифровывает КАЖДЫЙ и печатает его `expires_at` относительно
«сейчас». Значений ПДн не печатает — только сроки и число вхождений.

Ответ на вопрос «не вызывается / срок не истёк / ошибка» даёт таблица:
просроченный файл, лежащий на диске, = очистка не отработала; ни одного
просроченного = очистка отработала либо чистить было нечего.
"""
import json
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(_ROOT, "src"))

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

import storage  # noqa: E402
import session_store as SS  # noqa: E402
import vault  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    store = storage.default_storage_dir()
    print("хранилище:", store)
    print("сроки хранения:", storage.retention_settings())
    print("умолчание save_session (ч):", SS._DEFAULT_TTL_HOURS
          if hasattr(SS, "_DEFAULT_TTL_HOURS") else "см. сигнатуру save_session")

    key_path = store / SS._KEY_FILENAME
    if not key_path.exists():
        print("key.bin отсутствует — расшифровать нечем")
        return
    fernet = Fernet(vault.unwrap_key(key_path.read_bytes()))

    now = datetime.now(timezone.utc)
    rows = []
    for entry in sorted(store.iterdir()):
        if not entry.is_file() or entry.suffix != ".enc":
            continue
        try:
            data = json.loads(fernet.decrypt(entry.read_bytes()).decode("utf-8"))
            exp = datetime.fromisoformat(data["expires_at"])
            created = data.get("created_at", "?")
            rows.append((entry.name, created, exp, exp <= now, len(data.get("entities", []))))
        except (InvalidToken, json.JSONDecodeError, KeyError, ValueError) as exc:
            rows.append((entry.name, "?", None, None, f"НЕЧИТАЕМ: {type(exc).__name__}"))

    print("-" * 100)
    print("%-40s %-26s %-26s %s" % ("файл", "создана", "истекает", "просрочена?"))
    for name, created, exp, expired, n in rows:
        print("%-40s %-26s %-26s %s  (записей %s)" % (
            name, str(created)[:25], str(exp)[:25],
            "ДА" if expired else ("—" if expired is False else "?"), n))
    print("-" * 100)
    live = sum(1 for r in rows if r[3] is False)
    dead = sum(1 for r in rows if r[3] is True)
    print(f"всего .enc {len(rows)}: живых {live}, ПРОСРОЧЕННЫХ НА ДИСКЕ {dead}")
    print("сейчас:", now.isoformat(timespec="seconds"))


if __name__ == "__main__":
    main()
