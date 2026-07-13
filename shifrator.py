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


def cmd_encrypt(path, config_path):
    from extractor import extract
    from regex_detector import detect_regex
    from ner_detector import detect_ner
    from syntax_compound import merge_compound_entities
    from tokenizer import tokenize
    from session_store import save_session, default_storage_dir

    try:
        doc = extract(path)
    except FileNotFoundError:
        print(f"Ошибка: файл не найден: {path}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        entities = detect_regex(doc, config_path) + detect_ner(doc, config_path)
        # Волна 2, этап B: составные сущности «ORG + ФИО» (ИП Пирогова А.С.) —
        # отдельный синтаксический проход ПОСЛЕ основной детекции, ДО токенизации.
        entities = merge_compound_entities(doc, entities)
        anon_text, final_entities = tokenize(doc, entities, config_path)
    except FileNotFoundError:
        print(f"Ошибка: конфиг не найден: {config_path}", file=sys.stderr)
        sys.exit(1)

    session_id = save_session(final_entities, session_id=None, ttl_hours=24)

    storage_dir = default_storage_dir()
    (storage_dir / f"{session_id}.txt").write_text(anon_text, encoding="utf-8")

    print(session_id)


def cmd_decrypt(session_id):
    from session_store import purge_expired, SessionNotFoundError, SessionExpiredError
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
    from session_store import SessionNotFoundError, SessionExpiredError
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
    from session_store import delete_session

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
        cmd_encrypt(args.path, config_path)
    elif args.command == "decrypt":
        cmd_decrypt(args.session_id)
    elif args.command == "delete":
        cmd_delete(args.session_id)
    elif args.command == "decrypt-file":
        cmd_decrypt_file(args.session_id, args.path, args.out)


if __name__ == "__main__":
    main()
