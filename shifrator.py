import argparse
import os
import sys


def _default_config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "entity_types.yaml")


def cmd_encrypt(path, config_path):
    from pathlib import Path

    from extractor import extract
    from regex_detector import detect_regex
    from ner_detector import detect_ner
    from tokenizer import tokenize
    from session_store import save_session

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
        anon_text, final_entities = tokenize(doc, entities, config_path)
    except FileNotFoundError:
        print(f"Ошибка: конфиг не найден: {config_path}", file=sys.stderr)
        sys.exit(1)

    session_id = save_session(final_entities, session_id=None, ttl_hours=24)

    storage_dir = Path.home() / ".shifrator" / "sessions"
    (storage_dir / f"{session_id}.txt").write_text(anon_text, encoding="utf-8")

    print(session_id)


def cmd_decrypt(session_id):
    from session_store import purge_expired, SessionNotFoundError, SessionExpiredError
    from detokenizer import detokenize

    try:
        purge_expired()
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

    args = parser.parse_args()

    if args.command == "encrypt":
        config_path = args.config or _default_config_path()
        cmd_encrypt(args.path, config_path)
    elif args.command == "decrypt":
        cmd_decrypt(args.session_id)


if __name__ == "__main__":
    main()
