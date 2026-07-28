import argparse
import os
import sys

# Библиотечные модули проекта лежат в src/ (см. PROJECT_AUDIT.md, раздел
# «Структура»). Плоские имена импортов (from extractor import …) — публичный
# контракт всех блоков, поэтому src/ добавляется в sys.path, а не превращается
# в пакет. Вставка в позицию 1: позицию 0 занимает директория самого скрипта.
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(1, _SRC_DIR)


def _default_config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "entity_types.yaml")


def _read_strict_zones(config_path):
    """Читает ключ верхнего уровня strict_zones из конфига (этап 1b).

    Умолчание — True (fail-closed): отсутствующий ключ, отсутствующий файл и
    нечитаемый конфиг означают СТРОГИЙ режим. Ошибка чтения конфига не должна
    молча разблокировать потерю текста — это ровно та тихая деградация, против
    которой написан этап. Настоящую диагностику отсутствующего конфига даст
    detect_regex ниже, здесь мы её не дублируем.
    """
    import yaml

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return True
    return bool(config.get("strict_zones", True))


def _check_unread_zones(path, allow_lossy):
    """Политика непрочитанных зон (этап 1b). Возвращает список Zone.

    В strict-режиме поднимает типизированный UnreadZoneError (несёт сам список
    зон) — печать и exit code остаются заботой CLI-обёртки ниже. Так политика
    остаётся вызываемой не только из CLI: библиотечный потребитель получает
    исключение с данными, а не sys.exit в лицо.

    Порядок вызова важен: политика применяется ПОСЛЕ extract (файл уже проверен на
    «это вообще .docx» — не хотим два разных сообщения на битый файл) и ДО
    save_session, чтобы отказ не оставил после себя ни сессии, ни {sid}.txt.
    """
    from unread_zones import UnreadZoneError, scan_unread_zones, zones_table

    if not path.lower().endswith(".docx"):
        return []   # .txt читается целиком, зон в нём не бывает

    zones = scan_unread_zones(path)
    if not zones:
        return []

    if not allow_lossy:
        raise UnreadZoneError(zones, path)

    # lossy: громкое предупреждение, но работаем (пользователь попросил явно).
    print(
        f"ПРЕДУПРЕЖДЕНИЕ: {len(zones)} зон(ы) документа НЕ прочитаны и НЕ "
        f"анонимизированы — их текст отсутствует в результате:",
        file=sys.stderr,
    )
    print(zones_table(zones), file=sys.stderr)
    return zones


def _print_refusal(exc):
    """Отчёт об отказе: таблица зон на stdout (это запрошенный отчёт, а не
    диагностика) + подсказка, как действовать дальше."""
    from unread_zones import zones_table

    print(
        f"ОТКАЗ: документ содержит {len(exc.zones)} зон(ы), которые SHIFRATOR пока "
        f"не умеет читать. Их текст не был бы анонимизирован и молча пропал бы из "
        f"результата:\n"
    )
    print(zones_table(exc.zones))
    print(
        "\nАнонимизация НЕ выполнена. Варианты:\n"
        "  * перенести содержимое этих зон в тело документа и повторить;\n"
        "  * запустить с --allow-lossy — система обработает тело документа, а текст\n"
        "    зон запишет в {session_id}.unread.json рядом с сессией, чтобы вы видели,\n"
        "    что именно выброшено (ключ конфига strict_zones: false — то же самое)."
    )


def cmd_encrypt(path, config_path, allow_lossy=False):
    import type_policy
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize
    from storage import save_session, default_storage_dir
    from ooxml_core import OoxmlError
    from unread_zones import UnreadZoneError

    try:
        doc = extract(path)
    except FileNotFoundError:
        print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    # Флаг перекрывает конфиг: --allow-lossy включает lossy даже при strict_zones: true.
    if not allow_lossy:
        allow_lossy = not _read_strict_zones(config_path)
    try:
        zones = _check_unread_zones(path, allow_lossy)
    except UnreadZoneError as e:
        _print_refusal(e)
        sys.exit(2)
    except OoxmlError as e:
        # Битый контейнер: extract() выше его пропустил (python-docx оказался
        # снисходительнее), а сканер — нет. Сообщаем, а не молчим.
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # ЕДИНЫЙ конвейер детекции (этап B, консолидация): порядок шагов живёт в
        # pipeline.run_detection — та же функция, что зовут UI (app/core.py) и замер
        # (run_measurement.py). Копии порядка в этом файле больше нет (устраняет класс
        # UI-рассинхрона, см. src/pipeline.py).
        entities = run_detection(doc, config_path)
        # ЭТАП T1: детекция ПОЛНАЯ всегда; настройка «что маскировать» применяется
        # ВНУТРИ tokenize, после разрешения пересечений (см. src/type_policy.py —
        # выключенный тип обязан остаться барьером для границ соседних масок).
        anon_text, final_entities = tokenize(
            doc, entities, config_path, enabled_types=type_policy.enabled_types(config_path),
        )
    except FileNotFoundError:
        print(f"Ошибка: конфиг не найден: {config_path}", file=sys.stderr)
        sys.exit(1)

    session_id = save_session(final_entities, session_id=None, ttl_hours=24)

    storage_dir = default_storage_dir()
    (storage_dir / f"{session_id}.txt").write_text(anon_text, encoding="utf-8")

    if zones:
        # Sidecar lossy-режима. Он же — отметка сессии как lossy: формат
        # зашифрованного {sid}.enc есть контракт блока 5 (см. спецификацию и
        # tests/component2/test_f_session_contract.py), и расширять его ради
        # пометки нельзя. Отдельный файл рядом с {sid}.txt даёт то же самое —
        # «у этой сессии есть непрочитанное» — не трогая чужой контракт.
        #
        # Внутри — СЫРОЙ текст зон (ПДн открытым текстом). Это осознанно: смысл
        # файла в том, чтобы пользователь увидел, что именно выброшено. Наружу, в
        # LLM, уходит только {sid}.txt; sidecar остаётся локально, как и {sid}.enc.
        import json

        from unread_zones import zones_to_json

        sidecar = storage_dir / f"{session_id}.unread.json"
        sidecar.write_text(
            json.dumps(
                {"session_id": session_id, "source_path": os.path.abspath(path),
                 "lossy": True, "zones": zones_to_json(zones)},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"Текст непрочитанных зон сохранён: {sidecar}",
            file=sys.stderr,
        )

    print(session_id)


def cmd_decrypt(session_id):
    from storage import purge_expired, SessionNotFoundError, SessionExpiredError
    from detokenizer import detokenize

    # Автоочистка чужих просроченных сессий выполняется ВСЕГДА в начале decrypt —
    # независимо от того, какую сессию просит пользователь и найдётся ли она —
    # чтобы просроченные .enc не копились на диске бесконечно. Файл КОНКРЕТНО
    # запрошенной сессии исключаем из удаления (exclude_session_id): тогда, если
    # она истекла именно сейчас, load_session ниже кинет SessionExpiredError
    # («истекла»), а не SessionNotFoundError («не найдена»). Ошибки очистки не
    # прерывают decrypt — логируются в stderr, выполнение продолжается.
    try:
        purge_expired(exclude_session_id=session_id)
    except Exception as e:
        print(f"Предупреждение: purge_expired завершился с ошибкой: {e}", file=sys.stderr)

    print(
        "Введите текст ответа LLM, затем нажмите Ctrl+D (Linux/macOS) "
        "или Ctrl+Z и Enter (Windows):",
        file=sys.stderr,
    )
    text = sys.stdin.read()

    try:
        restored, unresolved = detokenize(text, session_id)
    except SessionNotFoundError:
        print(f"Ошибка: сессия не найдена: {session_id}", file=sys.stderr)
        sys.exit(1)
    except SessionExpiredError:
        print(f"Ошибка: сессия истекла: {session_id}", file=sys.stderr)
        sys.exit(1)

    print(restored)
    if unresolved:
        print(
            f"Предупреждение: не удалось восстановить токены: {', '.join(unresolved)}",
            file=sys.stderr,
        )


def cmd_decrypt_file(session_id, path, out):
    # Импорт лениво, внутри ветки подкоманды (соглашение блока 7): decrypt-file не
    # должен тянуть тяжёлые модули при обычном encrypt/decrypt. file_detokenizer сам
    # ничего тяжёлого не грузит — импорт мгновенный.
    from file_detokenizer import detokenize_file
    from storage import SessionNotFoundError, SessionExpiredError
    from ooxml_core import OoxmlError

    # В отличие от decrypt, purge_expired здесь НЕ вызывается — чтобы не менять
    # поведение по очистке в рамках аддитивной правки (см. спеку блока 12).
    try:
        dst_path, replaced, unresolved = detokenize_file(path, session_id, dst_path=out)
    except FileNotFoundError:
        print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
        sys.exit(1)
    except FileExistsError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except OoxmlError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except SessionNotFoundError:
        print(f"Ошибка: сессия не найдена: {session_id}", file=sys.stderr)
        sys.exit(1)
    except SessionExpiredError:
        print(f"Ошибка: сессия истекла: {session_id}", file=sys.stderr)
        sys.exit(1)

    print(dst_path)
    if unresolved:
        print(
            f"Предупреждение: не удалось восстановить токены: {', '.join(unresolved)}",
            file=sys.stderr,
        )
    if replaced == 0:
        # Файл валиден, но раскрывать было нечего. Это не ошибка (код возврата 0),
        # а сигнал: возможно, пользователь принёс не тот файл, либо токены лежат в
        # частях, которые компонент не смотрит (надписи w:txbxContent, коды полей
        # instrText — известное ограничение, см. блок 12 спецификации).
        print(
            "Предупреждение: в файле не найдено ни одного токена. Раскрывать было "
            "нечего — проверьте, что это именно тот файл, который вернула LLM.",
            file=sys.stderr,
        )


def cmd_delete(session_id):
    from storage import delete_session

    if delete_session(session_id):
        print(f"Сессия {session_id} удалена")
    else:
        print(f"Сессия {session_id} не найдена")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="shifrator.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    encrypt_parser = subparsers.add_parser("encrypt", help="анонимизировать документ")
    encrypt_parser.add_argument("path", help="путь к файлу .docx или .txt")
    encrypt_parser.add_argument(
        "--config", default=None, help="путь к entity_types.yaml (по умолчанию рядом с shifrator.py)"
    )
    encrypt_parser.add_argument(
        "--allow-lossy", action="store_true",
        help="обработать документ, даже если часть его текста система прочитать не умеет "
             "(колонтитулы, сноски, надписи, вложенные таблицы). Текст таких зон НЕ "
             "анонимизируется и в результат не попадает; он записывается в "
             "{session_id}.unread.json. По умолчанию такой документ отклоняется",
    )

    decrypt_parser = subparsers.add_parser("decrypt", help="восстановить текст ответа LLM")
    decrypt_parser.add_argument("session_id", help="session_id, полученный от encrypt")

    delete_parser = subparsers.add_parser("delete", help="удалить сессию по session_id")
    delete_parser.add_argument("session_id", help="session_id для удаления")

    decrypt_file_parser = subparsers.add_parser(
        "decrypt-file", help="раскрыть токены в файле .docx/.xlsx/.pptx с сохранением оформления"
    )
    decrypt_file_parser.add_argument("session_id", help="session_id, полученный от encrypt")
    decrypt_file_parser.add_argument("path", help="путь к файлу .docx, .xlsx или .pptx")
    decrypt_file_parser.add_argument(
        "--out", default=None, help="путь к выходному файлу (по умолчанию рядом с исходным, суффикс _restored)"
    )

    args = parser.parse_args()

    if args.command == "encrypt":
        config_path = args.config or _default_config_path()
        cmd_encrypt(args.path, config_path, allow_lossy=args.allow_lossy)
    elif args.command == "decrypt":
        cmd_decrypt(args.session_id)
    elif args.command == "delete":
        cmd_delete(args.session_id)
    elif args.command == "decrypt-file":
        cmd_decrypt_file(args.session_id, args.path, args.out)


if __name__ == "__main__":
    main()
