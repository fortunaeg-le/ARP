"""ЭТАП STORE — защита ключа хранилища и шифрование сайдкаров сессии.

Два отдельных вопроса, которые решаются здесь вместе, потому что оба про ОДИН
ключ:

1. **Где живёт ключ.** До этого этапа `key.bin` лежал 44 байтами Fernet-ключа
   ОТКРЫТЫМ ФАЙЛОМ ровно в той же папке, что и зашифрованные им сессии. Замок
   висел на двери вместе с ключом. Решение владельца — DPAPI Windows: ключ
   хранится завёрнутым средствами ОС в учётную запись пользователя, и без этой
   учётки файл `key.bin` — мусор.

   Что это даёт и чего НЕ даёт (формулировка согласована с владельцем и
   повторена пользователю в интерфейсе — `app/index.html`):
     * защищает: диск вынули, профиль скопировали на другую машину, зашли из
       другой учётной записи — расшифровать нельзя;
     * НЕ защищает: от вредоносной программы, запущенной под ЭТОЙ ЖЕ учёткой
       (для DPAPI она и есть пользователь);
     * цена: сессии не переносятся между машинами и учётками; после
       переустановки Windows старые сессии не открыть (скажем об этом внятно —
       см. `KeyUnavailableError`).

   Новых зависимостей не добавлено: DPAPI зовётся через `ctypes` из stdlib.

2. **Что шифруется.** Раньше шифровался ровно один файл сессии (`{sid}.enc`), а
   рядом с ним открытым текстом лежали пять других. Решение владельца — шифровать
   все шесть; обоснование по каждому:
     * `{sid}.doc.json` — ПОЛНЫЙ исходный текст документа (плюс вторая копия
       части сегментов в служебном кеше адресов);
     * `{sid}.unread.json` — сырой текст непрочитанных зон, сырой намеренно,
       значит это чистые ПДн;
     * `{sid}.txt` — обезличен НЕ полностью: детекция часть значений пропускает,
       это измеренный гейтом факт, а не осторожность;
     * `{sid}.meta.json` — имя исходного файла (именно оттуда однажды уехала
       реальная фамилия);
     * `{sid}.markup.json` — фрагменты реального текста;
     * `{sid}.enc` — шифровался и раньше.

Формат на диске (обе оболочки самоописываемы, чтобы читатель файла не гадал):

    key.bin           b"SHFR-DPAPI-1\\n" + blob DPAPI
    любой сайдкар     b"SHFR-ENC-1\\n"   + токен Fernet

Файл БЕЗ магии — старый, дошифровочный: читатели обязаны его принять
(инвариант «сессия старой версии открывается»), см. `read_maybe_encrypted`.
"""

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

#: Оболочка ключа: DPAPI-обёртка поверх сырого Fernet-ключа.
_KEY_MAGIC = b"SHFR-DPAPI-1\n"
#: Оболочка сайдкара: Fernet поверх исходных байт.
_BLOB_MAGIC = b"SHFR-ENC-1\n"

#: Дополнительная энтропия DPAPI — привязывает обёртку к ЭТОМУ приложению:
#: чужой процесс под той же учёткой должен знать её, чтобы развернуть ключ.
#: Барьером безопасности не является (строка лежит в исполняемом файле) и не
#: выдаётся за него — это ровно защита от случайного разворачивания.
_DPAPI_ENTROPY = b"SHIFRATOR/session-key/v1"


class KeyUnavailableError(Exception):
    """Ключ на диске есть, но развернуть его в ЭТОЙ системе нельзя.

    Штатная и объяснимая ситуация, а не поломка: профиль скопирован на другую
    машину, зашли из другой учётной записи, переустановлена Windows. Текст
    исключения обязан говорить это по-человечески — он доходит до пользователя
    через интерфейс.
    """


# --------------------------------------------------------------------------- #
# DPAPI через ctypes (без новых зависимостей)
# --------------------------------------------------------------------------- #

def dpapi_available() -> bool:
    """Есть ли DPAPI. На не-Windows его нет и быть не может."""
    return os.name == "nt"


def _dpapi_call(func_name: str, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(payload: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(payload, len(payload))
        return DATA_BLOB(len(payload), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    blob_in = _blob(data)
    entropy = _blob(_DPAPI_ENTROPY)
    blob_out = DATA_BLOB()

    func = getattr(crypt32, func_name)
    if func_name == "CryptProtectData":
        args = (ctypes.byref(blob_in), None, ctypes.byref(entropy),
                None, None, 0, ctypes.byref(blob_out))
    else:
        args = (ctypes.byref(blob_in), None, ctypes.byref(entropy),
                None, None, 0, ctypes.byref(blob_out))

    if not func(*args):
        raise KeyUnavailableError(
            f"{func_name} отказала (код {ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        # pbData выделен внутри crypt32 — освобождать обязаны мы, иначе течёт
        # память процесса на каждой операции с ключом.
        kernel32.LocalFree(blob_out.pbData)


def wrap_key(key: bytes) -> bytes:
    """Заворачивает сырой Fernet-ключ для записи в key.bin.

    На Windows — DPAPI с магией. На прочих ОС DPAPI не существует: возвращаем
    ключ КАК ЕСТЬ, без магии, то есть ровно старый формат. Это осознанный
    компромисс, а не забытая ветка — продукт целевой платформой имеет Windows
    (`docs/ARCHITECTURE.md`), а тихо выдавать на POSIX «защищено» было бы хуже
    отсутствия защиты. `key_protection()` сообщает вызывающему, что вышло на
    самом деле, и интерфейс показывает это пользователю честно.
    """
    if not dpapi_available():
        return key
    return _KEY_MAGIC + _dpapi_call("CryptProtectData", key)


def unwrap_key(raw: bytes) -> bytes:
    """Разворачивает содержимое key.bin в сырой Fernet-ключ.

    Файл БЕЗ магии — ключ, записанный до этого этапа (или на POSIX): отдаём как
    есть. Так открываются все сессии, созданные старой версией: сам Fernet-ключ
    при переезде на DPAPI НЕ меняется, меняется только его оболочка на диске.
    """
    if not raw.startswith(_KEY_MAGIC):
        return raw
    if not dpapi_available():
        raise KeyUnavailableError(
            "Ключ хранилища защищён средствами Windows (DPAPI), а программа "
            "запущена не в Windows — расшифровать сессии на этой системе нельзя."
        )
    try:
        return _dpapi_call("CryptUnprotectData", raw[len(_KEY_MAGIC):])
    except KeyUnavailableError as exc:
        raise KeyUnavailableError(
            "Ключ хранилища защищён средствами Windows и привязан к учётной "
            "записи, в которой были созданы сессии. Развернуть его в этой "
            "системе не удалось — так бывает, если профиль скопирован с другой "
            "машины, вход выполнен под другой учётной записью или Windows "
            "переустанавливали. Старые сессии восстановить нельзя; новые будут "
            "работать как обычно. "
            f"(техническая причина: {exc})"
        ) from exc


def key_protection() -> str:
    """Чем на этой системе защищён ключ: "dpapi" | "none". Для интерфейса —
    пользователю показывается ровно то, что есть, без обещаний."""
    return "dpapi" if dpapi_available() else "none"


# --------------------------------------------------------------------------- #
# Шифрование сайдкаров тем же ключом
# --------------------------------------------------------------------------- #

def encrypt_blob(key: bytes, data: bytes) -> bytes:
    """Fernet поверх данных + магия оболочки."""
    return _BLOB_MAGIC + Fernet(key).encrypt(data)


def is_encrypted(raw: bytes) -> bool:
    return raw.startswith(_BLOB_MAGIC)


def decrypt_blob(key: bytes, raw: bytes) -> bytes:
    """Разворачивает сайдкар. Файл БЕЗ магии — записанный старой версией
    открытым текстом: отдаём как есть (инвариант «старая сессия открывается»).

    Битый шифротекст — НЕ то же самое, что старый файл: InvalidToken
    пробрасывается наружу, молчаливой подмены на «пустой файл» здесь нет.
    """
    if not is_encrypted(raw):
        return raw
    return Fernet(key).decrypt(raw[len(_BLOB_MAGIC):])


def write_encrypted(path: Path, key: bytes, data: bytes | str) -> None:
    """Атомарная запись зашифрованного сайдкара (tmp + os.replace).

    Атомарность здесь не украшение: половина записанного шифротекста
    нечитаема целиком, а не частично, — недописанный файл сессии стоил бы
    пользователю всего документа.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(encrypt_blob(key, data))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_maybe_encrypted(path: Path, key: bytes) -> bytes:
    """Читает сайдкар, расшифровывая, если он зашифрован."""
    return decrypt_blob(key, path.read_bytes())
