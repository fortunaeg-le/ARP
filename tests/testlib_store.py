# -*- coding: utf-8 -*-
"""Чтение сайдкаров сессии в тестах (ЭТАП STORE).

С этого этапа `{sid}.txt` и `{sid}.unread.json` на диске зашифрованы, и тесты,
которые их проверяют, обязаны расшифровывать — иначе они сравнивали бы
шифротекст со строкой и падали бы (или, что хуже, «проходили» на пустоте).

Почему не `storage.load_anon_text`: у `storage` хранилище берётся из
`Path.home()` ТЕКУЩЕГО процесса, а тесты CLI запускают продукт подпроцессом с
ПОДМЕНЁННЫМ HOME и знают путь явно. Здесь путь передаётся аргументом, поэтому
одинаково годится и для CLI-, и для UI-пути.
"""

import json


def read_sidecar_bytes(sessions_dir, name):
    """Сырые байты сайдкара, расшифрованные ключом ЭТОГО хранилища."""
    import vault
    from session_store import storage_key

    key = storage_key(str(sessions_dir))
    return vault.read_maybe_encrypted(sessions_dir / name, key)


def read_anon_text(sessions_dir, sid):
    """Обезличенный текст сессии ({sid}.txt)."""
    return read_sidecar_bytes(sessions_dir, f"{sid}.txt").decode("utf-8")


def read_unread_sidecar(sessions_dir, sid):
    """Сайдкар непрочитанных зон ({sid}.unread.json) как разобранный JSON."""
    return json.loads(read_sidecar_bytes(sessions_dir, f"{sid}.unread.json").decode("utf-8"))
