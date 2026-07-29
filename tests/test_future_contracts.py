# -*- coding: utf-8 -*-
"""КОНТРАКТЫ БУДУЩИХ ЭТАПОВ — xfail-ожидания, которые этапы починки обязаны «погасить».

Эти тесты написаны ДО починки и НЕ тем, кто будет чинить: они фиксируют РЕЗУЛЬТАТ,
которого этап обязан достичь, чтобы починку нельзя было «подогнать» под тест.
Карта «дыра -> тест -> этап» — `docs/reports/FUTURE_CONTRACTS.md`.

КАК ЭТО РАБОТАЕТ
    Нерешённая дыра    -> @pytest.mark.xfail(strict=True).  Когда этап её закроет,
                          тест станет XPASS и УПАДЁТ — это заставит снять метку и
                          служит доказательством, что этап закрыл дыру, а не отчитался.
    Зеркало (passed)   -> обычный тест на значении, которое УЖЕ маскируется. Он
                          доказывает, что проверка умеет отличать «замаскировано» от
                          «утекло» и не красна по посторонней причине (документ не
                          загрузился, метрика слепа). Без зеркала xfail фиктивен.

ПРИНЦИПЫ (не отступать при правке этого файла)
  1. ТОЛЬКО ПУБЛИЧНЫЙ ВХОД. Тесты дёргают `shifrator.py encrypt` как процесс и
     проверяют анонимный текст `{session_id}.txt`. Ни одна проверка не завязана на
     внутренние функции/сигнатуры (`entity.start`, `detect_regex`, ...): будущие
     этапы меняют внутренности (Entity станет многодиапазонным, появятся новые
     детекторы), и тест, завязанный на них, был бы переписан при рефакторинге —
     ровно та подгонка, которую мы исключаем. Образец формы — существующий
     `tests/test_breaking_leaks.py::TestPhoneHomoglyphLeak` (этап 2, B4); он тут НЕ
     дублируется.
  2. ДАННЫЕ — ТОЛЬКО ИЗ ЗАМОРОЖЕННОГО КОРПУСА (`tests/corpus/gold.json`), синтетика
     не выдумывается. Значения берутся через `_gold_entity()`, который падает, если
     сущность из корпуса исчезла: тест обязан умереть громко, а не тихо проверять
     пустоту.
  3. МЕТРИКА — `leak_v2` из `tests/corpus/measure_lib.py` (нормализованное сравнение:
     невидимые сняты, омоглифы сведены, цифры склеены), а НЕ наивный `in`: иначе
     омоглиф/невидимый символ обманули бы проверку. Исключение — ADDRESS, см.
     `_address_names_surviving()` и раздел «ЗАМЕР» ниже.

ЗАМЕР: почему ADDRESS не проверяется через `leak_v2_address` (важно, не «оптимизировать»)
    `measure_lib.leak_v2_address()` даёт ЛОЖНЫЕ СРАБАТЫВАНИЯ и для контракта не
    годится. Проверено на `supply_0001` (коммит a118c84): адрес «141300, г. Сергиев
    Посад, пер. Ленина, д. 7, кв. 12» полностью замаскирован ([ADDRESS_2] в тексте,
    «Ленина» и «141300» отсутствуют), а метрика рапортует partial с фрагментами
    ['0','12','7','пep'] — потому что (а) буквенные токены ищутся ПОДСТРОКОЙ, и «пер»
    (сокращение «переулок», которого нет в стоп-списке) находится внутри обычного
    слова «передать» (в v2-норме «пepeдatь»); (б) номер дома 1-4 цифры находится внутри любого числа
    документа. Связка «дом + буквенный токен» срабатывает почти на любом тексте.
    Тест на такой метрике был бы красным по НЕВЕРНОЙ причине и НИКОГДА бы не
    позеленел после починки границ адреса. Поэтому ниже — своя строгая проверка на
    ТЕХ ЖЕ нормализаторах measure_lib (омоглифы/невидимые), но по ЦЕЛЫМ СЛОВАМ и
    только по идентифицирующим именам (город/улица) и почтовому индексу.
    См. FUTURE_CONTRACTS.md, раздел «Дефект измерительного прибора».
"""
import collections
import json
import os
import re
import subprocess
import sys

import pytest

from testlib_policy import pin_all_types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS_DIR = os.path.join(_PROJECT_ROOT, "tests", "corpus")
_DOCS_DIR = os.path.join(_CORPUS_DIR, "docs")
_SHIFRATOR = os.path.join(_PROJECT_ROOT, "shifrator.py")

if _CORPUS_DIR not in sys.path:
    sys.path.insert(0, _CORPUS_DIR)

import measure_lib as ML  # noqa: E402  (после правки sys.path)

_GOLD = {d["doc_id"]: d for d in
         json.load(open(os.path.join(_CORPUS_DIR, "gold.json"), encoding="utf-8"))}


# --------------------------------------------------------------------------- #
#                        Публичный вход: encrypt как процесс                   #
# --------------------------------------------------------------------------- #
EncryptRun = collections.namedtuple("EncryptRun", "returncode anon stderr session_id home")


@pytest.fixture(scope="session")
def encrypt_doc(tmp_path_factory):
    """Гоняет `shifrator.py encrypt` над документом корпуса и отдаёт анонимный текст.

    Хранилище сессий изолировано подменой USERPROFILE/HOME на tmp (правило STATE.md
    §7: реальный ~/.shifrator в тестах НЕ трогать). Результат кэшируется на сессию
    по (doc_id, allow_lossy): каждый прогон encrypt платит холодный старт natasha
    (~секунды), а один и тот же документ нужен нескольким тестам.
    """
    cache = {}

    def _run(doc_id, allow_lossy=True):
        key = (doc_id, allow_lossy)
        if key in cache:
            return cache[key]
        fmt = _GOLD[doc_id]["format"]
        path = os.path.join(_DOCS_DIR, "{}.{}".format(doc_id, fmt))
        home = tmp_path_factory.mktemp("home")
        # ЭТАП T1: контракты этого файла — про ДЕТЕКТОРЫ (ИНН с битой КС, СНИЛС,
        # разрыв через ячейку), а не про набор типов по умолчанию. Набор пришпилен
        # к «Максимуму» тем же файлом настроек, которым пользуется человек.
        pin_all_types(home)
        env = dict(os.environ, PYTHONIOENCODING="utf-8",
                   USERPROFILE=str(home), HOME=str(home))
        args = [sys.executable, _SHIFRATOR, "encrypt", path]
        if allow_lossy:
            args.append("--allow-lossy")
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              env=env, cwd=_PROJECT_ROOT)
        anon = session_id = None
        if proc.returncode == 0:
            session_id = proc.stdout.strip()
            anon = (home / ".shifrator" / "sessions" /
                    "{}.txt".format(session_id)).read_text(encoding="utf-8")
        cache[key] = EncryptRun(proc.returncode, anon, proc.stderr, session_id, home)
        return cache[key]

    return _run


def _decrypt(run, text):
    """`shifrator.py decrypt` над тем же (временным) хранилищем; отдаёт stdout."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               USERPROFILE=str(run.home), HOME=str(run.home))
    proc = subprocess.run([sys.executable, _SHIFRATOR, "decrypt", run.session_id],
                          input=text, capture_output=True, text=True, encoding="utf-8",
                          env=env, cwd=_PROJECT_ROOT)
    assert proc.returncode == 0, "decrypt упал: {}".format(proc.stderr)
    return proc.stdout


def _anon(run):
    """Анонимный текст успешного encrypt; громко падает, если encrypt не отработал.

    Нужен, чтобы xfail не мог «покраснеть» из-за отказа/креша, оставшись
    незамеченным: такой тест провалится с внятным сообщением, а не по проверке утечки.
    """
    assert run.returncode == 0, (
        "encrypt не отработал (rc={}), проверка утечки недостижима: {}"
        .format(run.returncode, (run.stderr or "")[:300])
    )
    return run.anon


def _gold_entity(doc_id, entity_type, text):
    """Сущность замороженного корпуса по (документ, тип, точное значение).

    Падает, если её нет: корпус заморожен (MANIFEST.sha256), и его изменение обязано
    ломать тесты громко, а не превращать их в проверку пустоты.
    """
    hits = [e for e in _GOLD[doc_id]["entities"]
            if e["type"] == entity_type and e["text"] == text]
    assert hits, ("в gold.json нет {} {!r} у {} — корпус изменился, тест устарел"
                  .format(entity_type, text, doc_id))
    return hits[0]


# --------------------------------------------------------------------------- #
#                     Метрика утечки: leak_v2 из measure_lib                   #
# --------------------------------------------------------------------------- #
# Диспетчер повторяет run_measurement.leak_v2 (тот же порог strict=8/soft=6, та же
# ветка на тип). Он здесь, а не импортом из run_measurement, потому что тот модуль —
# скрипт: на импорте он создаёт временное хранилище и тянет весь конвейер.
_V2_NUMERIC_TYPES = {"INN", "INN_PER", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE", "PASSPORT", "SNILS"}


def _leak(entity_type, gold_text, anon):
    """{status: none|partial|full, fragments: [...]} — что от эталонного значения
    дожило до анонимного текста (сравнение нормализованное: невидимые сняты,
    омоглифы сведены, цифры склеены)."""
    if entity_type == "BIRTHDATE":
        return ML.leak_v2_birthdate(gold_text, ML.v2_date_field(anon))
    if entity_type in _V2_NUMERIC_TYPES:
        return ML.leak_v2_numeric(gold_text, ML.v2_digit_runs(anon), strict=8, soft=6)
    if entity_type in ("PER", "ORG"):
        return ML.leak_v2_per(gold_text, ML.v2_norm_text(anon))
    if entity_type == "EMAIL":
        return ML.leak_v2_email(gold_text, ML.v2_norm_text(anon))
    raise AssertionError(
        "для типа {} нет ветки leak_v2; ADDRESS проверяется _address_names_surviving()"
        .format(entity_type))


def _assert_masked(entity_type, gold_text, anon, what):
    v2 = _leak(entity_type, gold_text, anon)
    assert v2["status"] == "none", (
        "{}: значение {!r} дожило до анонимного текста ({}), фрагменты {}"
        .format(what, gold_text, v2["status"], v2.get("fragments")))


# Служебные слова адреса: тип улицы/населённого пункта, сокращения, номера частей.
# Их выживание никого не идентифицирует и утечкой не считается.
_ADDR_KIND_WORDS = {
    "улица", "переулок", "просп", "проспект", "бульвар", "набережная", "шоссе",
    "проезд", "площадь", "аллея", "линия", "гор", "город", "область", "край",
    "респ", "республика", "район", "посёлок", "поселок", "деревня", "село",
    "микрорайон", "автодороги", "дом", "корп", "корпус", "стр", "строение",
    "владение", "квартира", "помещение", "офис", "литера", "этаж", "абонентский",
    "ящик", "почтовый", "россия", "российская", "федерация",
}


def _address_names_surviving(gold_text, anon):
    """Идентифицирующие части адреса, дожившие до анонимного текста.

    Возвращает список вида ['ИНДЕКС:344002', 'ИМЯ:лесная']; пусто — адрес не утёк.
    Считается утечкой: (а) почтовый индекс — 6-значный блок эталона, выживший в
    цифровом поле; (б) ИМЯ — буквенный токен эталона >= 4 символов, не служебное
    слово, выживший ЦЕЛЫМ СЛОВОМ. Нормализация — v2 из measure_lib (омоглифы и
    невидимые не спасают от проверки). Почему не `leak_v2_address` — см. докстринг
    модуля, раздел «ЗАМЕР».
    """
    anon_norm = ML.v2_norm_text(anon)
    whole_words = set(re.findall(r"[^\W\d_]+", anon_norm, re.UNICODE))
    digit_field = ML.v2_digit_runs(anon)
    survived = []
    for block in re.findall(r"\d+", ML._v2_strip_invisible(gold_text)):
        if len(block) == 6 and block in digit_field:
            survived.append("ИНДЕКС:" + block)
    for token in ML.alpha_tokens_v2(gold_text, 4):
        if token in _ADDR_KIND_WORDS or token in ML._V2_ADDR_STOP:
            continue
        if token in whole_words:
            survived.append("ИМЯ:" + token)
    return sorted(set(survived))


# =========================================================================== #
# ЭТАП 3 — СНИЛС, дата рождения, код подразделения паспорта: детектора НЕТ
# =========================================================================== #
# Фикстура (BIRTHDATE, зеркало ИНН этапа 4): services_0001 — .txt, features=[]
# (единственный класс документов корпуса без единого состязательного приёма).
# Значит утечка не может объясняться омоглифом, невидимым символом, разрывом или
# регистром — только отсутствием детектора.
# Первоисточник дыры: docs/reports/COVERAGE_MEASUREMENT.md §3 «no_detector_for_type»
# (SNILS 228, BIRTHDATE 228) и «no_detector_passport_subdiv» (305).
_S3_DOC = "services_0001"
_S3_BIRTHDATE = "21.01.1960"
_S3_INN = "215707879877"

# --- поправка после этапа 2 (нормализация) ---
# Фикстуры SNILS и кода подразделения на services_0001 (СНИЛС «793-234-941 33»,
# код «812-400») стали XPASS не потому, что появился детектор, а потому, что
# нормализация ИНЦИДЕНТНО замаскировала именно ЭТОТ документ (он попал в
# маскируемую часть корпуса — см. reason ниже). xfail на нём был бы фиктивным:
# строгий (strict=True) тест обязан падать на СВОЕЙ проверке (значение выжило как
# СНИЛС/код подразделения), а не быть случайно зелёным. Замер по всему корпусу
# (см. FINDINGS.md, Stage2-N): СНИЛС утекает открытым текстом в 36/228 сущностей
# (15.8%), код подразделения — в 35/248 (14.1%). Фикстуры ниже взяты из этого
# утекающего остатка, ПРОВЕРЕНО прогоном encrypt (2026-07-18).
_S3_SNILS_DOC = "supply_0003__m1337_digit_spaces"  # ОДИН приём (mut:digit_spaces)
_S3_SNILS_TEXT = "003-219\xa0-113\xa0 82"           # СНИЛС здесь утекает full (не маскируется)
_S3_SNILS_INN_MIRROR = "9916 80 33 0 9"             # ИНН, валидная КС, тот же документ — маскируется

_S3_SUBDIV_DOC = "supply_0003"          # .txt, features=[] — БЕЗ единого состязательного приёма
_S3_SUBDIV_CODE = "051-237"             # код подразделения — утекает full даже на чистом документе
_S3_SUBDIV_SERIES = "02 08 829967"      # серия+номер — детектор ЕСТЬ (зеркало), маскируется


class TestStage3SnilsHasNoDetector:
    """СНИЛС не ищется ничем (`grep -i snils src/` пуст) и утекает открытым текстом.

    ПОПРАВКА к этапу 2: раньше фикстурой был services_0001 — он попал в те ~84%
    корпуса, что нормализация ИНЦИДЕНТНО замаскировала (схлопывание дефисов даёт
    9 цифр, которые ловит KPP), и xfail на нём был бы зелёным по чужой причине, а
    не по устранению дыры. Документ заменён на supply_0003__m1337_digit_spaces
    (ОДИН приём — mut:digit_spaces): проверено прогоном encrypt (2026-07-18), СНИЛС
    здесь входит в оставшиеся ~16% корпуса, где нормализация не подобрала цифровой
    прогон под чужой детектор, и значение утекает в анонимном тексте ЦЕЛИКОМ
    («003-219 -113 82 » рядом с уже замаскированным [INN_2])."""

    # ЭТАП 3 ПОГАСИЛ ЭТОТ КОНТРАКТ (метка xfail снята). Появился типизированный
    # детектор SNILS: якорь «СНИЛС» + форма ddd-ddd-ddd dd, span_group=1 (маскируется
    # значение БЕЗ якоря). Тест из ожидания дыры стал пришпиливающим: если СНИЛС снова
    # начнёт утекать, он покраснеет.
    def test_snils_value_does_not_survive_anonymization(self, encrypt_doc):
        gold = _gold_entity(_S3_SNILS_DOC, "SNILS", _S3_SNILS_TEXT)
        anon = _anon(encrypt_doc(_S3_SNILS_DOC))
        _assert_masked("SNILS", gold["text"], anon, "СНИЛС")

    def test_mirror_inn_in_same_document_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО. Тот же документ, тот же прогон encrypt, ТА ЖЕ числовая ветка
        метрики (leak_v2_numeric): ИНН с валидной контрольной суммой замаскирован.
        Значит документ читается, encrypt работает, а метрика умеет сказать «none» —
        и xfail выше красен из-за СНИЛС, а не из-за поломанного окружения.

        Зеркала «СНИЛС на чистом документе уже маскируется» не существует в природе:
        детектора SNILS нет вовсе, тип типологически не маскируется НИГДЕ в корпусе
        (это всегда чужой детектор случайно). Ближайшее честное зеркало — соседний
        числовой реквизит того же документа."""
        gold = _gold_entity(_S3_SNILS_DOC, "INN", _S3_SNILS_INN_MIRROR)
        anon = _anon(encrypt_doc(_S3_SNILS_DOC))
        _assert_masked("INN", gold["text"], anon, "зеркало ИНН")

    def test_snils_value_is_restorable_after_decrypt(self, encrypt_doc):
        """Контракт round-trip, который починка обязана СОХРАНИТЬ. Сегодня проходит
        вырожденно (СНИЛС не маскируется, поэтому «восстанавливается» сам собой);
        когда этап 3 научится его маскировать, decrypt обязан вернуть исходное
        значение, а не потерять его. Тест — страховка от «замаскировали и потеряли»."""
        run = encrypt_doc(_S3_SNILS_DOC)
        restored = _decrypt(run, _anon(run))
        assert _S3_SNILS_TEXT in restored, "decrypt не вернул исходный СНИЛС"


class TestStage3BirthdateHasNoDetector:
    """Дата рождения не ищется: тип DATE в entity_types.yaml есть, но `enabled: false`
    (COVERAGE_MEASUREMENT.md §3). Проверено на a118c84: «Дата рождения: 21.01.1960»."""

    # ЭТАП 3 ПОГАСИЛ ЭТОТ КОНТРАКТ (метка xfail снята). Появился тип BIRTHDATE:
    # дата ПОД ЯКОРЕМ РОЖДЕНИЯ (не общий детектор дат — он обвалил бы precision на
    # датах подписания/выдачи), маскируется span_group=1 — сама дата без якоря.
    def test_birthdate_value_does_not_survive_anonymization(self, encrypt_doc):
        gold = _gold_entity(_S3_DOC, "BIRTHDATE", _S3_BIRTHDATE)
        anon = _anon(encrypt_doc(_S3_DOC))
        _assert_masked("BIRTHDATE", gold["text"], anon, "дата рождения")

    def test_mirror_birthdate_metric_discriminates(self, encrypt_doc):
        """ЗЕРКАЛО для ветки ДАТЫ (leak_v2_birthdate + v2_date_field): она РАЗЛИЧАЕТ,
        а не всегда молчит. Без этого «none» в тесте выше ничего не стоил бы.

        ПРАВКА ЭТАПА 3. Раньше «положительной» стороной зеркала был сам утекающий
        BIRTHDATE в анонимном тексте (`present == full`) — то есть зеркало опиралось
        на ДЫРУ, и этап, закрывший дыру, ронял его. Теперь «положительная» сторона
        берётся из ИСХОДНОГО текста документа, где дата есть заведомо и всегда:
          * исходный текст   -> full  (метрика умеет сказать «утекло»);
          * анонимный текст  -> none  (значение замаскировано);
          * чужая дата корпуса на том же анонимном тексте -> none (не ложная тревога).
        Все три стороны нужны: без первой метрика могла бы быть всегда-молчащей, без
        третьей — всегда-кричащей."""
        other = _gold_entity("agency_0003", "BIRTHDATE", "10.07.1996")
        gold = _gold_entity(_S3_DOC, "BIRTHDATE", _S3_BIRTHDATE)
        raw = open(os.path.join(_DOCS_DIR, "{}.{}".format(_S3_DOC, _GOLD[_S3_DOC]["format"])),
                   encoding="utf-8").read()
        anon = _anon(encrypt_doc(_S3_DOC))
        assert other["text"] not in anon, "чужая дата всё же есть в тексте — зеркало непригодно"
        assert _leak("BIRTHDATE", gold["text"], raw)["status"] == "full", \
            "ветка даты не видит дату в ИСХОДНОМ тексте — метрика всегда-молчащая, зеркало непригодно"
        assert _leak("BIRTHDATE", other["text"], anon)["status"] == "none", \
            "ветка даты кричит об утечке того, чего в тексте нет"
        assert _leak("BIRTHDATE", gold["text"], anon)["status"] == "none", \
            "дата рождения дожила до анонимного текста"


class TestStage3PassportSubdivisionHasNoDetector:
    """Детектор PASSPORT ловит только «серия+номер»; вторая паспортная сущность —
    код подразделения (ддд-ддд) — не ищется ничем (305 пропусков,
    COVERAGE_MEASUREMENT.md §3).

    ПОПРАВКА к этапу 2: раньше фикстурой был services_0001 («код подразделения
    812-400») — он попал в маскируемую часть корпуса (нормализация инцидентно
    отдала его код ADDRESS/ORG-спану), и xfail на нём был бы зелёным по чужой
    причине. Документ заменён на supply_0003 (.txt, features=[] — БЕЗ единого
    состязательного приёма): проверено прогоном encrypt (2026-07-18), код
    подразделения «051-237» утекает открытым текстом даже на этом чистом
    документе — дыра не требует никакого искажения, чтобы проявиться."""

    # ЭТАП 3 ПОГАСИЛ ЭТОТ КОНТРАКТ (метка xfail снята). У типа PASSPORT появился
    # второй паттерн — код подразделения под якорем «код подразделения», span_group=1.
    # Голый «ддд-ддд» без якоря по-прежнему НЕ берётся (это мусор: кусок телефона/индекса).
    def test_passport_subdivision_code_does_not_survive(self, encrypt_doc):
        gold = _gold_entity(_S3_SUBDIV_DOC, "PASSPORT", _S3_SUBDIV_CODE)
        anon = _anon(encrypt_doc(_S3_SUBDIV_DOC))
        _assert_masked("PASSPORT", gold["text"], anon, "код подразделения паспорта")

    def test_mirror_passport_series_and_number_in_same_document_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО — самое сильное из возможных: ТОТ ЖЕ ТИП (PASSPORT), тот же
        документ, тот же прогон. Серия+номер маскируется, код подразделения — нет.
        Значит дыра именно в охвате детектора паспорта, а не в документе, конвейере
        или метрике."""
        gold = _gold_entity(_S3_SUBDIV_DOC, "PASSPORT", _S3_SUBDIV_SERIES)
        anon = _anon(encrypt_doc(_S3_SUBDIV_DOC))
        _assert_masked("PASSPORT", gold["text"], anon, "зеркало: серия+номер паспорта")


# =========================================================================== #
# ЭТАП 4 — невалидная контрольная сумма: КС как сигнал, а не шлагбаум [ПОГАШЕНО]
# =========================================================================== #
# Фикстура: lease_0003 — .txt с features=['invalid_checksum','oblique_names',
# 'phone_zoo']. Корпус назначает контрольную сумму ПОДОКУМЕНТНО: документа, где у
# одного типа есть и valid, и invalid, не существует — поэтому зеркало «тот же тип с
# валидной КС» берётся из чистого services_0001, а внутридокументное зеркало даёт
# ACCOUNT (см. ниже).
# Первоисточник: COVERAGE_MEASUREMENT.md §3 «rejected_invalid_checksum» (164) —
# для 152-ФЗ такой номер всё равно ПДн, отбраковка = утечка. Этап 4 добавил
# `anchor:` (src/regex_detector.py `_has_anchor`): под якорем («ИНН»/«ОГРН» слева)
# КС больше не решает — значение маскируется в любом случае. Все проверки ниже —
# документ lease_0003 честно анкорирует ИНН/ОГРН/счёт словом-триггером
# («ИНН 721566853972», «ОГРНИП 1480094441508», «р/с 40702810160368220597»).
_S4_DOC = "lease_0003"
_S4_INN_INVALID = "721566853972"
_S4_OGRN_INVALID = "1480094441508"
_S4_ACCOUNT_INVALID = "40702810160368220597"


class TestStage4InvalidChecksumIsRejectedAndLeaks:
    """ЭТАП 4 ПОГАСИЛ ВЕСЬ КЛАСС (см. HANDOFF_STAGE_4). Было: `entity_types.yaml`
    вешал `validate:` на INN/OGRN как БЕЗУСЛОВНЫЙ фильтр — номер с не сошедшейся КС
    детектор ОТБРАСЫВАЛ, и значение оставалось в открытом тексте. Проверено на
    a118c84: «ОГРН 1480094441508»,
    «ИНН 721566853972» видны в анонимном тексте."""

    # ЭТАП 4 ПОГАСИЛ ЭТОТ КОНТРАКТ (метка xfail снята). Под якорем «ИНН» значение
    # маскируется независимо от КС (src/regex_detector.py `_has_anchor`).
    def test_inn_with_invalid_checksum_is_still_masked(self, encrypt_doc):
        # ЭТАП T2-INN: значение 12-значное, то есть ИНН ФИЗЛИЦА, и в эталоне
        # лежит под типом INN_PER. Сам контракт (КС — не шлагбаум) не изменился.
        gold = _gold_entity(_S4_DOC, "INN_PER", _S4_INN_INVALID)
        assert gold["checksum"] == "invalid"
        anon = _anon(encrypt_doc(_S4_DOC))
        _assert_masked("INN_PER", gold["text"], anon, "ИНН физлица с невалидной КС")

    # ЭТАП 4 ПОГАСИЛ ЭТОТ КОНТРАКТ (метка xfail снята). Под якорем «ОГРН»/«ОГРНИП»
    # значение маскируется независимо от КС.
    def test_ogrn_with_invalid_checksum_is_still_masked(self, encrypt_doc):
        gold = _gold_entity(_S4_DOC, "OGRN", _S4_OGRN_INVALID)
        assert gold["checksum"] == "invalid"
        anon = _anon(encrypt_doc(_S4_DOC))
        _assert_masked("OGRN", gold["text"], anon, "ОГРН с невалидной КС")

    def test_mirror_account_with_invalid_checksum_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО, изолирующее ПРИЧИНУ. Тот же документ, то же условие «КС в gold
        помечена invalid» — но у BANK_ACCOUNT в entity_types.yaml нет ключа
        `validate:`, отбраковывать некому, и счёт маскируется. Значит утечка INN/OGRN
        выше вызвана именно ВАЛИДАТОРОМ, а не «странностью» самих номеров и не
        документом.

        Из этого же следует, почему на ACCOUNT НЕ повешен xfail: дыры для него нет,
        xfail был бы XPASS и упал бы сразу."""
        gold = _gold_entity(_S4_DOC, "ACCOUNT", _S4_ACCOUNT_INVALID)
        assert gold["checksum"] == "invalid"
        anon = _anon(encrypt_doc(_S4_DOC))
        _assert_masked("ACCOUNT", gold["text"], anon, "зеркало: счёт с невалидной КС")

    def test_mirror_inn_with_valid_checksum_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО «тот же тип на чистом каноническом документе»: ИНН с ВАЛИДНОЙ КС в
        services_0001 маскируется. Вместе с xfail выше это запирает причину с двух
        сторон: детектор ИНН работает, ломает его ровно невалидная контрольная сумма."""
        gold = _gold_entity(_S3_DOC, "INN_PER", _S3_INN)   # 12 цифр, этап T2-INN
        assert gold["checksum"] == "valid"
        anon = _anon(encrypt_doc(_S3_DOC))
        _assert_masked("INN_PER", gold["text"], anon, "зеркало: ИНН с валидной КС")

    def test_invalid_checksum_values_are_restorable_after_decrypt(self, encrypt_doc):
        """Round-trip-контракт, который этап 4 обязан СОХРАНИТЬ: перестав
        отбраковывать номер, нельзя потерять его при восстановлении."""
        run = encrypt_doc(_S4_DOC)
        restored = _decrypt(run, _anon(run))
        assert _S4_INN_INVALID in restored, "decrypt не вернул ИНН"
        assert _S4_OGRN_INVALID in restored, "decrypt не вернул ОГРН"


# =========================================================================== #
# ЭТАП 5 — ADDRESS: адрес без маркеров не находится и утекает именами
# =========================================================================== #
# Фикстура: works_0009 — .txt, приём `dense_line`: адрес записан без маркеров
# («Красногорск  Гагарина 74» — ни «г.», ни «ул.», ни индекса), склеен в плотную
# строку реквизитов. Проверено на a118c84: строка «...КПП [KPP_1] Красногорск
# Гагарина 74 тел [PHONE_2]» — вокруг всё токенизировано, адрес цел.
#
# ВАЖНО про формулировку этапа. Замер (COVERAGE_MEASUREMENT.md §4, H6) описывает дыру
# как «ADDRESS: recall 89%, но span 37.6% и leak 76.6% — адрес утекает компонентами».
# Проверка на реальных документах это НЕ подтверждает: у ugly-адресов, которые метрика
# объявляет утёкшими, found=True и full=True — они замаскированы целиком, а «утечка» —
# ложное срабатывание leak_v2_address (см. докстринг модуля, раздел «ЗАМЕР»). Реальные
# утечки адреса в чистых base-документах — это НЕ границы, а адрес, не найденный вовсе
# (`dense_line`). Контракт написан на настоящую дыру.
_S5_DOC = "works_0009"
_S5_ADDRESS_DENSE = "Красногорск  Гагарина 74"
_S5_ADDRESS_MASKED = ("344002, Новосибирская обл., г. Ростов-на-Дону, ш. Лесная, "
                      "д. 29, корп. 2, литера А, пом. 30, оф. 238")


class TestStage5AddressWithoutMarkersLeaks:
    """Адрес без маркеров («город улица дом» в плотной строке).

    ЭТАП C-fix (2026-07-23): xfail СНЯТ — house-pattern-якорь (`_addr_span_strong`) +
    сеяние по house-pattern (`_addr_has_seed`) ТЕПЕРЬ детектируют безмаркерный
    склеенный «Красногорск  Гагарина 74» (Titlecase-топоним + улица-топоним +
    номер дома, ≥2 топонима). Город и улица больше НЕ утекают. Контракт из xfail стал
    обычным регресс-барьером: если правка снова начнёт ронять этот класс — покраснеет.
    NB: голые ALL-CAPS/мутационные безмаркерные адреса всё ещё в долге
    (docs/known_leaks_stage_c.json) — этот тест про ЧИСТЫЙ dense_line."""

    def test_marker_less_address_names_do_not_survive(self, encrypt_doc):
        gold = _gold_entity(_S5_DOC, "ADDRESS", _S5_ADDRESS_DENSE)
        anon = _anon(encrypt_doc(_S5_DOC))
        survived = _address_names_surviving(gold["text"], anon)
        assert not survived, (
            "адрес {!r} утёк идентифицирующими частями: {}".format(gold["text"], survived))

    def test_mirror_address_with_markers_in_same_document_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО. Тот же документ, тот же прогон, ТА ЖЕ проверка
        `_address_names_surviving`: адрес С маркерами и индексом замаскирован целиком —
        ни индекс «344002», ни «Ростов»/«Дону»/«Лесная» не дожили. Значит проверка
        различает «замаскировано» и «утекло» (исторически подтверждала, что тест выше
        был красен из-за отсутствия маркеров, а не из-за всегда-красной проверки; после
        этапа C-fix оба теста зелёные — dense_line тоже маскируется).

        Улицы намеренно разные: «Гагарина» встречается в документе дважды, и зеркало
        на ней было бы отравлено утечкой соседа."""
        gold = _gold_entity(_S5_DOC, "ADDRESS", _S5_ADDRESS_MASKED)
        anon = _anon(encrypt_doc(_S5_DOC))
        survived = _address_names_surviving(gold["text"], anon)
        assert not survived, "зеркальный адрес неожиданно утёк: {}".format(survived)


# =========================================================================== #
# ЭТАП 6 — непрочитанные зоны .docx
# =========================================================================== #
# ПОЧЕМУ КОНТРАКТ НЕ «значение из зоны не выживает» (главная ловушка этого файла).
# Сегодня зона НЕ ЧИТАЕТСЯ, поэтому её текст не попадает в анонимный результат вовсе —
# и «утечки» нет. Замер это подтверждает: in_header 35/35 found=False, leak=False;
# in_textbox 32/32 без утечки (COVERAGE_MEASUREMENT.md §4: «leak сноски/колонтитула
# ненулевой не потому, что зона прочитана, а потому что то же значение есть в теле»).
# Проверка «значение из зоны не выжило» СЕГОДНЯ ЗЕЛЁНАЯ — впустую: она зелена не
# потому, что система защитила ПДн, а потому что выбросила кусок документа. Как xfail
# она дала бы XPASS и упала бы сразу; как обычный тест — вечно зелёная пустышка.
#
# Контракт написан на то, что этап 6 обязан дать НАБЛЮДАЕМО (STATE.md §3: «Научиться
# читать — этап 6»), тремя проверками одного прогона:
#   1. encrypt в СТРОГОМ режиме отрабатывает (rc=0). Сегодня rc=2: этап 1 научил
#      систему ОТКАЗЫВАТЬСЯ от документа с непрочитанной зоной. Прочитанная зона —
#      это непрочитанной зоны нет — это отказа нет. Публичный, не выдуманный признак.
#   2. Безобидный якорь зоны (не ПДн) виден в анонимном тексте — зона именно
#      ПРОЧИТАНА, а не просто перестала мешать. Каждый якорь проверен: сегодня его в
#      выводе нет, то есть он приходит только из зоны.
#   3. ПДн зоны не выжили. Сегодня недостижимо (падаем на 1), после этапа 6 — суть.
# Тест гаснет, только если выполнено ВСЁ. Прочитали зону и утекли — тест останется
# красным, и это правильно.
ZoneCase = collections.namedtuple("ZoneCase", "doc_id kind anchor entity_type value")

# По одному .docx РОВНО С ОДНОЙ зоной на каждый вид — чтобы «починили колонтитулы, но
# не сноски» было видно поштучно, а не тонуло в общем отказе документа с двумя зонами.
_ZONE_CASES = [
    ZoneCase("cession_0002", "header", "·", "PHONE", "+7 (499) 125-61-37"),
    ZoneCase("agency_0004", "footer", "Исполнитель документа", "PHONE", "8(383)1784068"),
    ZoneCase("agency_0006", "footnote", "Реквизиты для возврата", "INN", "6584461879"),
    ZoneCase("agency_0008", "textbox", "ВНИМАНИЕ! Бухгалтерия", "PER", "Семёнов Сергей Егорович"),
    ZoneCase("agency_0010", "nested_table", "Контакт", "ACCOUNT", "40802810717011235378"),
]


class TestStage6UnreadZones:

    @pytest.mark.parametrize("case", _ZONE_CASES, ids=[c.kind for c in _ZONE_CASES])
    @pytest.mark.xfail(strict=True, reason="этап 6: зона .docx не читается — encrypt отказывает (strict), текст зоны не обрабатывается")
    def test_zone_is_read_and_its_pii_is_masked(self, encrypt_doc, case):
        gold = _gold_entity(case.doc_id, case.entity_type, case.value)
        run = encrypt_doc(case.doc_id, allow_lossy=False)
        assert run.returncode == 0, (
            "encrypt отказал (rc={}) — зона {} по-прежнему непрочитана"
            .format(run.returncode, case.kind))
        anon = run.anon
        assert case.anchor in anon, (
            "зона {} не попала в анонимный текст: якоря {!r} нет"
            .format(case.kind, case.anchor))
        _assert_masked(case.entity_type, gold["text"], anon, "ПДн из зоны " + case.kind)

    def test_mirror_docx_without_zones_is_processed_strictly_and_masked(self, encrypt_doc):
        """ЗЕРКАЛО. supply_0004 — .docx с features=[] и БЕЗ единой зоны: в том же
        строгом режиме encrypt отрабатывает (rc=0), а ПДн маскируются. Значит red выше
        даёт именно непрочитанная зона, а не .docx-конвейер, не строгий режим и не
        метрика."""
        run = encrypt_doc("supply_0004", allow_lossy=False)
        assert run.returncode == 0, (
            "docx без зон обязан обрабатываться в строгом режиме: rc={}, {}"
            .format(run.returncode, (run.stderr or "")[:200]))
        # Тип не важен для смысла зеркала (проверяется, что .docx-конвейер в
        # строгом режиме маскирует ПДн), но после этапа T2-INN канонические ИНН
        # этого документа — 12-значные, то есть INN_PER.
        inn = [e for e in _GOLD["supply_0004"]["entities"]
               if e["type"] in ("INN", "INN_PER") and e["category"] == "canonical"][0]
        _assert_masked(inn["type"], inn["text"], run.anon, "зеркало: ИНН в docx без зон")


# =========================================================================== #
# РАЗРЫВЫ ВНУТРИ СУЩНОСТИ (будущий Entity.spans)
# =========================================================================== #
# Дыра РАЗНАЯ по под-случаям — глухой xfail на «разрывы» был бы неправдой:
#   * cell_boundary_split — РАБОТАЕТ (recall 92.6%, mut:cell_split 100%): механизм B3
#     (граничное окно) разрыв ячейки закрывает. Ниже — зеркало, не xfail.
#   * linebreak_inside_entity — сломан избирательно: PER через перенос ловится
#     (found 13/13), ACCOUNT — нет (found 0/15). xfail только на счёте.
# Первоисточник: COVERAGE_MEASUREMENT.md §4 (H4 «опровергнута в части recall»).
_SPLIT_DOC = "agency_0007"
_SPLIT_ACCOUNT = "407028106\n00849653120"
_SPLIT_PER_DOC = "cession_0003"
_SPLIT_PER = "Сидоров\nМихаил Михайлович"
_SPLIT_CELL_DOC = "labor_0004"
_SPLIT_CELL_INN = "1100\t56673254"


class TestSplitEntitiesAcrossLineBreak:
    """Счёт, разорванный переносом строки, не собирается: первые 9 цифр
    («407028106») подхватывает ЧУЖОЙ детектор KPP (9-значный шаблон) и токенизирует,
    а хвост из 11 цифр остаётся открытым текстом. Проверено на a118c84:
    «Счёт для возврата: [KPP_1]\\n00849653120»."""

    @pytest.mark.xfail(strict=True, reason="разрывы (Entity.spans): счёт через перенос строки не собирается — хвост из 11 цифр утекает")
    def test_account_split_by_linebreak_does_not_survive(self, encrypt_doc):
        gold = _gold_entity(_SPLIT_DOC, "ACCOUNT", _SPLIT_ACCOUNT)
        assert gold["trick"] == "linebreak_inside_entity"
        anon = _anon(encrypt_doc(_SPLIT_DOC))
        _assert_masked("ACCOUNT", gold["text"], anon, "счёт через перенос строки")

    def test_mirror_person_split_by_same_linebreak_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО. ТОТ ЖЕ приём (linebreak_inside_entity) на ФИО — маскируется.
        Значит перенос строки сам по себе конвейер не ломает, и xfail выше — про
        сборку ЧИСЛОВОГО реквизита, а не про переносы вообще."""
        gold = _gold_entity(_SPLIT_PER_DOC, "PER", _SPLIT_PER)
        assert gold["trick"] == "linebreak_inside_entity"
        anon = _anon(encrypt_doc(_SPLIT_PER_DOC))
        _assert_masked("PER", gold["text"], anon, "зеркало: ФИО через перенос строки")

    def test_mirror_inn_split_by_cell_boundary_is_masked(self, encrypt_doc):
        """ЗЕРКАЛО и одновременно защита от регресса механизма B3: ИНН, разорванный
        ГРАНИЦЕЙ ЯЧЕЙКИ, собирается граничным окном и маскируется.

        Почему тут нет xfail, хотя замер показывает утечку у части
        cell_boundary_split-сущностей: проверка на документах (a118c84) показала, что
        те «утечки» — ЧУЖИЕ. У agency_0006 ни одна половина ИНН («5697», «36761884»)
        в анонимном тексте не встречается, а метрика находит ядро, потому что ТО ЖЕ
        значение есть в документе ещё раз — омоглифной копией (у sale_0010 это видно
        прямо: «Контрольный ИНН: І226865248» с кириллической «І»). Это дефект B4
        (этап 2), а не разрыв ячейки. xfail здесь пометил бы чужую дыру своим этапом."""
        gold = _gold_entity(_SPLIT_CELL_DOC, "INN_PER", _SPLIT_CELL_INN)  # 12 цифр
        assert gold["trick"] == "cell_boundary_split"
        anon = _anon(encrypt_doc(_SPLIT_CELL_DOC))
        _assert_masked("INN_PER", gold["text"], anon, "зеркало: ИНН через границу ячейки")

    def test_split_account_is_restorable_after_decrypt(self, encrypt_doc):
        """Round-trip-контракт для будущего многодиапазонного Entity: собрав
        разорванное значение в один токен, decrypt обязан вернуть исходный текст
        документа — с переносом строки внутри значения ровно там, где он был."""
        run = encrypt_doc(_SPLIT_DOC)
        restored = _decrypt(run, _anon(run))
        assert _SPLIT_ACCOUNT in restored, "decrypt не вернул разорванный счёт как в оригинале"
