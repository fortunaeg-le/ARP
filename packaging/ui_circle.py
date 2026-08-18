"""КРУГ ЧЕРЕЗ ИНТЕРФЕЙС — шагами настоящего пользователя (этап CIRCLE-UI, задача 1).

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Круг приёмки (`accept_circle.py`) ходил по HTTP API
собранной программы — то есть по ТОМУ ЖЕ пути, что командная строка. Экран он не
открывал ни разу, и поэтому пропустил два дефекта подряд:

  * PDF читался движком с этапа PDF-ARCH, а интерфейс его НЕ ПУСКАЛ вовсе:
    круг слал байты на `/api/encrypt` напрямую и ограничения экрана не видел;
  * предупреждение о непрочитанных страницах PDF лежало в ответе сервера
    (`pdf_issues`) и потому круг считал его показанным — а на ЭКРАНЕ его не было
    НИКОГДА. Договор со страницей-сканом обрабатывался, страница молча не
    читалась, человек считал документ обезличенным.

Вывод, ради которого написан этот модуль: **«есть в ответе сервера» ≠ «видно
человеку»**. Здесь проверяется ровно второе — то, что отрисовано в браузере.

КАК ЭТО РАБОТАЕТ, БЕЗ ЕДИНОЙ СТОРОННЕЙ БИБЛИОТЕКИ. Программа офлайн, ставить
selenium/playwright нельзя. Браузер (Edge или Chrome — оба стоят на любой
рабочей Windows) поднимается в фоновом режиме и управляется по его штатному
протоколу отладки (CDP). Протокол идёт по WebSocket, которого нет в стандартной
библиотеке, поэтому ниже лежит МИНИМАЛЬНЫЙ клиент WebSocket на `socket` —
рукопожатие и текстовые кадры, больше ничего не нужно. В сеть не уходит ничего:
и браузер, и сервер программы живут на 127.0.0.1, фоновые запросы браузера
погашены флагами.

ФАЙЛЫ В БРАУЗЕР кладутся так же, как их кладёт человек: в поле `<input
type=file>` подставляется настоящий `File`, собранный из байтов, и рассылается
`change` — тот самый обработчик, что срабатывает от выбора файла мышью.
Выделение значения — настоящий `Range` в панели плюс настоящий `mouseup`, то
есть тот же путь, что у протяжки мышью.

Запускается из `accept_circle.py`; отдельно — для отладки:
    venv\\Scripts\\python.exe packaging\\ui_circle.py --port 8765
"""
import base64
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Браузеры, которые ищем. Ставить ничего не надо: Edge есть на любой Windows 10+,
# Chrome — если пользователь его поставил. Порядок — от «точно есть» к «может быть».
_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class UiCircleError(RuntimeError):
    """Круг через интерфейс не смог выполнить шаг."""


def find_browser() -> str | None:
    for p in _BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Минимальный клиент WebSocket (RFC 6455) на стандартной библиотеке.
# Нужен ровно для CDP: рукопожатие, отправка текстового кадра, приём кадра.
# Расширений, фрагментации на отправке и бинарных кадров здесь нет намеренно —
# CDP их не использует, а лишний код в приёмочном инструменте это лишний риск.
# --------------------------------------------------------------------------- #
class _WebSocket:
    def __init__(self, url: str, timeout: float = 30.0):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        if not m:
            raise UiCircleError(f"не разобрал адрес отладки браузера: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        self._buf = b""
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise UiCircleError("браузер оборвал рукопожатие отладки")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n")[0]:
            raise UiCircleError(f"браузер отказал в отладочном подключении: {head[:200]!r}")

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise UiCircleError("браузер закрыл отладочное соединение")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str):
        payload = text.encode("utf-8")
        header = bytearray([0x81])          # FIN + opcode=text
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            masked = bool(b1 & 0x80)
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x1:               # текст — единственное, что шлёт CDP
                return data.decode("utf-8")
            if opcode == 0x8:               # close
                raise UiCircleError("браузер закрыл отладочное соединение (close)")
            # ping/pong/continuation — CDP их не шлёт; молча пропускаем

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Браузер: запуск в фоне и разговор по CDP
# --------------------------------------------------------------------------- #
class Browser:
    def __init__(self, exe: str, profile_dir: str):
        self.port = free_port()
        self.profile_dir = profile_dir
        self.proc = subprocess.Popen(
            [exe,
             "--headless=new",
             "--disable-gpu",
             f"--remote-debugging-port={self.port}",
             f"--user-data-dir={profile_dir}",
             # ЭТАП DESIGN-1: размер окна теперь ЗАДАЁТСЯ, а не берётся по
             # умолчанию. С адаптивом интерфейс ниже 900 px честно уступает
             # место заглушке «Нужен экран пошире» (app/index.html, класс
             # w-stub), а безголовый Edge по умолчанию даёт 800x600 —
             # innerWidth 770. Круг при этом падал на шаге 22 не потому, что
             # программа сломана, а потому, что мерил её на ширине, которую
             # продукт не обслуживает. 1280x900 — «комфортно работать» из
             # текста самой заглушки.
             "--window-size=1280,900",
             "--no-first-run", "--no-default-browser-check",
             # Программа офлайн — гасим ВСЁ фоновое, что браузер иначе тянет в сеть.
             "--disable-background-networking", "--disable-component-update",
             "--disable-sync", "--disable-default-apps", "--disable-extensions",
             "--no-service-autorun", "--metrics-recording-only",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.ws = None
        self._id = 0
        self._connect()

    def _connect(self, timeout: float = 40.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                pages = [t for t in targets if t.get("type") == "page"
                         and t.get("webSocketDebuggerUrl")]
                if pages:
                    self.ws = _WebSocket(pages[0]["webSocketDebuggerUrl"])
                    return
            except (urllib.error.URLError, ConnectionError, OSError, ValueError) as e:
                last = e
            time.sleep(0.3)
        raise UiCircleError(f"браузер не поднял отладочный порт за {timeout:.0f} с ({last})")

    def cmd(self, method: str, params: dict | None = None, timeout: float = 60.0):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != mid:
                continue                    # событие CDP — нам не нужно
            if "error" in msg:
                raise UiCircleError(f"{method}: {msg['error']}")
            return msg.get("result", {})
        raise UiCircleError(f"{method}: браузер не ответил за {timeout:.0f} с")

    def navigate(self, url: str):
        self.cmd("Page.enable")
        self.cmd("Page.navigate", {"url": url})

    def js(self, expression: str, timeout: float = 60.0):
        """Выполняет выражение НА СТРАНИЦЕ и возвращает его значение.

        `awaitPromise` — чтобы шаги могли ждать сеть страницы её же средствами
        (`await fetch(...)`), а не гаданием по таймеру."""
        res = self.cmd("Runtime.evaluate", {
            "expression": f"(async()=>{{ {expression} }})()",
            "awaitPromise": True, "returnByValue": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            det = res["exceptionDetails"]
            text = (det.get("exception") or {}).get("description") or det.get("text")
            raise UiCircleError(f"ошибка на странице: {text}")
        return res.get("result", {}).get("value")

    def wait_js(self, expression: str, what: str, timeout: float = 90.0, every: float = 0.4):
        """Ждёт, пока выражение на странице не станет истинным. Иначе — падение."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.js(f"return ({expression});")
            if last:
                return last
            time.sleep(every)
        raise UiCircleError(f"не дождался: {what} (за {timeout:.0f} с; последнее значение {last!r})")

    def close(self):
        if self.ws is not None:
            self.ws.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# --------------------------------------------------------------------------- #
# Вспомогательное: подсунуть файл в поле выбора файла ТАК ЖЕ, как это делает
# человек. Не «дёрнуть внутреннюю функцию», а положить настоящий File в
# input.files и разослать change — сработает ровно тот обработчик, что и от мыши.
# --------------------------------------------------------------------------- #
_PUT_FILE_JS = """
const b64=%(b64)s, name=%(name)s, sel=%(sel)s;
const bin=atob(b64); const arr=new Uint8Array(bin.length);
for(let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
const f=new File([arr], name);
const dt=new DataTransfer(); dt.items.add(f);
const input=document.querySelector(sel);
if(!input) return 'нет поля '+sel;
input.files=dt.files;
input.dispatchEvent(new Event('change',{bubbles:true}));
return 'ok';
"""


def put_file(br: Browser, selector: str, filename: str, data: bytes):
    out = br.js(_PUT_FILE_JS % {
        "b64": json.dumps(base64.b64encode(data).decode()),
        "name": json.dumps(filename),
        "sel": json.dumps(selector),
    })
    if out != "ok":
        raise UiCircleError(f"файл не удалось положить в поле: {out}")


# Виден ли элемент ЧЕЛОВЕКУ: не только есть в DOM, но и не скрыт, имеет размер
# и непустой текст. Ровно этой проверки не хватало вчера — плашка о непрочитанных
# страницах PDF существовала в разметке и была скрыта классом hidden всегда.
VISIBLE_JS = """
const el=document.querySelector(%s);
if(!el) return null;
const st=getComputedStyle(el), r=el.getBoundingClientRect();
if(st.display==='none'||st.visibility==='hidden'||+st.opacity===0) return null;
if(r.width<1||r.height<1) return null;
return (el.innerText||el.textContent||'').trim();
"""


def visible_text(br: Browser, selector: str):
    """Текст элемента, если человек его ВИДИТ; иначе None."""
    return br.js(VISIBLE_JS % json.dumps(selector))


# --------------------------------------------------------------------------- #
# САМ КРУГ ЧЕРЕЗ ИНТЕРФЕЙС
#
# Каждый шаг проверяет РЕЗУЛЬТАТ НА ЭКРАНЕ, а не факт «страница открылась».
# `report(ok, name, detail)` приходит из accept_circle.py — там же общий счётчик
# шагов, чтобы у круга был один сквозной номер, а не два независимых.
# --------------------------------------------------------------------------- #
def run_ui_circle(port: int, report, note, samples: dict):
    """samples: {'txt': bytes, 'docx': bytes, 'pdf_ok': bytes, 'pdf_scan': bytes}"""
    exe = find_browser()
    report(exe is not None, "браузер для круга через интерфейс найден",
           os.path.basename(exe) if exe else
           "ни Edge, ни Chrome не найдены — экран проверить нечем")

    profile = tempfile.mkdtemp(prefix="shifrator_uicircle_")
    br = None
    try:
        br = Browser(exe, profile)
        url = f"http://127.0.0.1:{port}/"
        br.navigate(url)
        br.wait_js("document.readyState==='complete' && !!document.getElementById('drop')",
                   "страница интерфейса отрисовалась")
        title = visible_text(br, "#scr-enc h2")
        report(bool(title), "страница интерфейса открыта в браузере", repr(title))

        # --- ШАГ, КОТОРЫЙ ПОЙМАЛ БЫ ВЧЕРАШНИЙ ДЕФЕКТ №1 --------------------- #
        # PDF читался движком, а экран о нём не знал и файл не пускал. Проверяем
        # ИМЕННО ЭКРАН: что человек читает под кнопкой и что примет поле выбора.
        br.wait_js("typeof CAPS!=='undefined' && !!CAPS",
                   "экран получил список возможностей от программы")
        shown = br.wait_js("(document.getElementById('cap-input-list')||{}).textContent",
                           "подпись форматов заполнена")
        engine_exts = br.js("return (CAPS.input_formats||[]).map(f=>f.ext);")
        missing = [e for e in engine_exts if e not in (shown or "")]
        report(not missing and ".pdf" in (shown or ""),
               "ВСЕ форматы движка НАЗВАНЫ человеку на экране",
               f"на экране: «{shown}»" + (f"; не названо: {missing}" if missing else ""))

        accept = br.js("return document.getElementById('file').getAttribute('accept')||'';")
        not_accepted = [e for e in engine_exts if e not in accept]
        report(not not_accepted and ".pdf" in accept,
               "поле выбора файла ПРИНИМАЕТ всё, что читает движок",
               f"accept=«{accept}»" + (f"; не принимается: {not_accepted}" if not_accepted else ""))

        # --- загрузка .txt шагами человека ---------------------------------- #
        put_file(br, "#file", "agency_0003.txt", samples["txt"])
        br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
                   "экран проверки открылся после загрузки .txt", timeout=300)
        count_txt = visible_text(br, "#v-count")
        n_txt = int(re.search(r"\d+", count_txt or "0").group())
        report(n_txt > 0, "ЧИСЛО СПРЯТАННЫХ ЗНАЧЕНИЙ ВИДНО на экране (.txt)",
               f"«{count_txt}»")

        listed = br.js("return document.querySelectorAll('#v-list .item, #v-list [data-token]').length "
                       "|| (document.getElementById('v-list').innerText||'').trim().length;")
        report(bool(listed), "СПИСОК НАЙДЕННОГО виден на экране", f"непустой ({listed})")

        anon_on_screen = visible_text(br, "#pane-anon")
        report(bool(anon_on_screen) and "[" in anon_on_screen,
               "ОБЕЗЛИЧЕННЫЙ ТЕКСТ виден на экране, в нём стоят метки",
               f"{len(anon_on_screen or '')} символов")

        sid = br.js("return (typeof CUR!=='undefined' && CUR ? CUR.session_id : '')||'';")
        report(bool(sid), "ID сессии показан на экране", sid[:8] + "…")

        # --- пометить пропущенное значение ЖЕСТОМ --------------------------- #
        # Настоящее выделение (Range) в панели + настоящий mouseup: срабатывает
        # тот же обработчик, что у протяжки мышью, а не внутренняя функция.
        picked = br.js(SELECT_IN_ANON_JS)
        report(isinstance(picked, str) and picked.startswith("ok:"),
               "значение в панели ВЫДЕЛЕНО жестом пользователя", str(picked))
        br.wait_js("document.getElementById('markup-menu').style.display==='block'",
                   "плашка выбора типа появилась на экране", timeout=20)
        menu_text = visible_text(br, "#markup-menu")
        report(bool(menu_text), "ПЛАШКА ВЫБОРА ТИПА ВИДНА человеку",
               (menu_text or "").split("\n")[0][:60])

        before = n_txt
        br.js("const b=document.querySelector('#markup-menu button.mm-type');"
              "if(!b) return 'нет кнопок типов'; b.click(); return 'ok';")
        br.wait_js("(document.getElementById('markup-toast').innerText||'').trim().length>0",
                   "программа ответила на пометку", timeout=30)
        toast = visible_text(br, "#markup-toast")
        report(bool(toast), "ОТВЕТ НА ПОМЕТКУ ВИДЕН на экране", (toast or "")[:80])

        # --- применить правки ------------------------------------------------ #
        # Кнопка «Применить правки» доступна ТОЛЬКО когда правки накоплены
        # (index.html: disabled = pendingCount===0). То, что она стала активной, —
        # и есть подтверждение человеку, что его пометку приняли.
        br.wait_js("!document.getElementById('markup-apply-btn').disabled",
                   "кнопка «Применить правки» стала доступна человеку", timeout=60)
        report(True, "кнопка «ПРИМЕНИТЬ ПРАВКИ» СТАЛА ДОСТУПНА на экране",
               "пометка принята, есть что применять")
        br.js("document.getElementById('markup-apply-btn').click(); return 'ok';")
        br.wait_js("/Применено|применять нечего/.test("
                   "document.getElementById('markup-toast').innerText||'')",
                   "пересборка документа закончилась и о ней сказано на экране", timeout=300)
        after = int(re.search(r"\d+", visible_text(br, "#v-count") or "0").group())
        applied_toast = visible_text(br, "#markup-toast")
        report(after > before,
               "ПРАВКА ПРИМЕНЕНА и результат виден на экране: масок стало больше",
               f"было {before}, стало {after}; на экране: «{(applied_toast or '')[:70]}»")

        anon_after = br.js("return (typeof CUR!=='undefined' && CUR ? CUR.anon_text : '')||'';")

        # --- вернуть ответ и получить восстановленный текст ------------------ #
        br.js("show('res'); return 'ok';")
        br.wait_js("document.getElementById('scr-res').classList.contains('active')",
                   "экран восстановления открылся")
        br.js("document.getElementById('r-sid').value=%s;"
              "document.getElementById('r-text').value=%s; return 'ok';"
              % (json.dumps(sid), json.dumps(anon_after)))
        br.js("document.querySelector('#scr-res button.primary').click(); return 'ok';")
        br.wait_js("!document.getElementById('res-out').classList.contains('hidden')",
                   "восстановленный текст появился на экране", timeout=180)
        restored_on_screen = visible_text(br, "#r-restored")
        res_msg = visible_text(br, "#res-msg")
        leftover = len(re.findall(r"\[[A-Z_]+_\d+\]", restored_on_screen or ""))
        report(bool(restored_on_screen) and leftover == 0,
               "ВОССТАНОВЛЕННЫЙ ТЕКСТ ВИДЕН на экране, нераскрытых меток нет",
               f"{len(restored_on_screen or '')} символов, меток осталось {leftover}; "
               f"строка состояния: «{(res_msg or '')[:60]}»")
        report(all(v in (restored_on_screen or "") for v in EXPECTED_ON_SCREEN_TXT),
               "спрятанные значения ВЕРНУЛИСЬ и видны человеку",
               f"проверено значений: {len(EXPECTED_ON_SCREEN_TXT)}")

        return br, sid
    except Exception:
        if br is not None:
            br.close()
        raise


# Значения, которые обязаны ВЕРНУТЬСЯ И БЫТЬ ВИДНЫ на экране восстановления.
# Список короткий намеренно (полноту меряет гейт): круг держит смысл —
# «значение спрятано и возвращено», и держит его ГЛАЗАМИ ЧЕЛОВЕКА.
EXPECTED_ON_SCREEN_TXT = ["13 10 006794", "+7 (495) 409-87-66"]

# Выделение значения в панели ЖЕСТОМ, а не вызовом внутренней функции.
# Берём непомеченный фрагмент (span.lit — там координаты исходного сегмента),
# строим настоящий Range на кусок его текста и рассылаем настоящий mouseup:
# дальше работает ровно тот путь, что у протяжки мышью (index.html::fireSelection).
SELECT_IN_ANON_JS = """
const pane=document.getElementById('pane-anon');
if(!pane) return 'нет панели обезличенного текста';
let node=null, from=0, to=0;
for(const lit of pane.querySelectorAll('span.lit')){
  const t=lit.firstChild;
  if(!t || t.nodeType!==3) continue;
  const m=/[A-Za-zА-Яа-яЁё]{5,}/.exec(t.nodeValue||'');
  if(!m) continue;
  node=t; from=m.index; to=m.index+m[0].length; break;
}
if(!node) return 'в панели не нашлось непомеченного слова для выделения';
const r=document.createRange();
r.setStart(node, from); r.setEnd(node, to);
const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
const box=node.parentElement.getBoundingClientRect();
document.dispatchEvent(new MouseEvent('mouseup',
  {bubbles:true, clientX:Math.round(box.left+2), clientY:Math.round(box.top+2)}));
return 'ok:'+node.nodeValue.slice(from,to);
"""

# Забрать файл, который браузер уже отдал человеку кнопкой «Скачать», ОБРАТНО в
# Python — чтобы открыть его настоящим python-docx и убедиться, что оформление и
# таблица целы. Ссылка ведёт на blob:, поэтому читаем её через fetch+FileReader
# прямо на странице: это тот же байт-в-байт файл, который получит человек.
GRAB_BLOB_JS = """
const a=document.getElementById('rf-link');
if(!a || !a.href || a.href.indexOf('blob:')!==0) return null;
const b=await (await fetch(a.href)).blob();
const buf=new Uint8Array(await b.arrayBuffer());
let s=''; const CH=0x8000;
for(let i=0;i<buf.length;i+=CH) s+=String.fromCharCode.apply(null, buf.subarray(i,i+CH));
return btoa(s);
"""


def run_ui_circle_files(br, report, note, samples: dict, tmp_dir: str):
    """Продолжение круга: .docx, ВОЗВРАТ ФАЙЛОМ с оформлением и PDF.

    Вынесено отдельной функцией только ради читаемости — это тот же проход того
    же браузера, счётчик шагов сквозной.
    """
    br.js("show('enc'); resetEnc(); return 'ok';")
    br.wait_js("document.getElementById('scr-enc').classList.contains('active')",
               "экран загрузки открылся снова")
    put_file(br, "#file", "agency_0002.docx", samples["docx"])
    # У .docx бывают непрочитанные зоны — тогда экран сначала СПРАШИВАЕТ. Это
    # штатная развилка, и человек на ней жмёт «обработать всё равно».
    br.wait_js("document.getElementById('scr-ver').classList.contains('active') || "
               "!document.getElementById('zones-box').classList.contains('hidden')",
               "экран ответил на загрузку .docx", timeout=300)
    if br.js("return !document.getElementById('zones-box').classList.contains('hidden');"):
        zt = visible_text(br, "#zones-box")
        report(bool(zt), "ПРЕДУПРЕЖДЕНИЕ О НЕПРОЧИТАННЫХ ЗОНАХ .docx ВИДНО на экране",
               (zt or "").replace("\n", " ")[:90])
        clicked = br.js(
            "const b=[...document.querySelectorAll('#zones-box button')]"
            ".find(x=>/всё равно|все равно|продолж|обработать/i.test(x.textContent));"
            "if(!b) return 'нет кнопки согласия'; b.click(); return 'ok';")
        report(clicked == "ok", "человек согласился обработать документ неполно", str(clicked))
        br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
                   "экран проверки открылся после согласия на неполное чтение", timeout=300)
    count_docx = visible_text(br, "#v-count")
    n_docx = int(re.search(r"\d+", count_docx or "0").group())
    report(n_docx > 0, "ЧИСЛО СПРЯТАННЫХ ЗНАЧЕНИЙ ВИДНО на экране (.docx)", f"«{count_docx}»")
    sid_docx = br.js("return (typeof CUR!=='undefined' && CUR ? CUR.session_id : '')||'';")
    anon_docx = br.js("return (typeof CUR!=='undefined' && CUR ? CUR.anon_text : '')||'';")

    # --- ЗАДАЧА 2: ВЕРНУТЬ ДОГОВОР ФАЙЛОМ, ЧЕРЕЗ ЭКРАН ---------------------- #
    # Имитируем то, что реально приносит юрист: ответ нейросети — это ИСХОДНЫЙ
    # .docx, в котором значения заменены метками. Оформление и таблицы при этом
    # авторские — их и проверяем на выходе.
    llm_docx = samples["docx_with_tokens"](sid_docx)
    br.js("show('res'); return 'ok';")
    br.wait_js("document.getElementById('scr-res').classList.contains('active')",
               "экран восстановления открылся")
    rf_accept = br.js("return document.getElementById('rf-file').getAttribute('accept')||'';")
    report(".docx" in rf_accept and ".xlsx" in rf_accept and ".pptx" in rf_accept,
           "поле возврата файлом ПРИНИМАЕТ все форматы движка", f"accept={rf_accept}")
    br.js("document.getElementById('rf-sid').value=%s; return 'ok';" % json.dumps(sid_docx))
    put_file(br, "#rf-file", "answer.docx", llm_docx)
    br.js("const b=[...document.querySelectorAll('#scr-res button.primary')]"
          ".find(x=>/файл/i.test(x.textContent));"
          "if(!b) return 'нет кнопки возврата файлом'; b.click(); return 'ok';")
    br.wait_js("!document.getElementById('rf-out').classList.contains('hidden') || "
               "/err/.test(document.getElementById('rfile-status').className)",
               "программа ответила на возврат файлом", timeout=180)
    rf_msg = visible_text(br, "#rfile-msg")
    link = visible_text(br, "#rf-link")
    note_txt = visible_text(br, "#rf-note")
    report(bool(link), "ССЫЛКА НА ВОССТАНОВЛЕННЫЙ ФАЙЛ ВИДНА на экране",
           f"[{link}]; состояние: [{(rf_msg or '')[:70]}]")
    replaced = int(re.search(r"\d+", note_txt or "0").group())
    report(replaced > 0, "на экране НАЗВАНО, сколько значений возвращено в файл",
           f"{replaced}; [{(note_txt or '').replace(chr(10), ' ')[:80]}]")

    b64 = br.js(GRAB_BLOB_JS, timeout=120)
    report(bool(b64), "файл ЗАБРАН из браузера так же, как его получит человек",
           f"{len(b64 or '') * 3 // 4} байт")
    out_path = os.path.join(tmp_dir, "restored_from_ui.docx")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))
    return sid_docx, anon_docx, out_path


def check_pdf_refused_as_file(br, report, samples: dict, sid: str):
    """ЧЕСТНЫЙ ОТКАЗ по PDF: не пустая кнопка и не поломка, а НАЗВАННАЯ дорога.

    Решение владельца: восстановление PDF возвращается в ответ, а не в файл.
    Человек обязан прочитать это на экране, иначе он решит, что программа сломалась.
    """
    br.js("show('res'); return 'ok';")
    br.js("document.getElementById('rf-sid').value=%s; return 'ok';" % json.dumps(sid))
    put_file(br, "#rf-file", "answer.pdf", samples["pdf_ok"])
    br.js("const b=[...document.querySelectorAll('#scr-res button.primary')]"
          ".find(x=>/файл/i.test(x.textContent)); b.click(); return 'ok';")
    # Ждём КОНЕЦ работы, а не первое же слово: строка «Возвращаю значения в
    # файл…» тоже непустая, и наивная проверка «текст появился» ловила её.
    br.wait_js("!/busy/.test(document.getElementById('rfile-status').className) && "
               "(document.getElementById('rfile-msg').innerText||'').length>0",
               "программа ответила на попытку вернуть PDF файлом", timeout=60)
    msg = visible_text(br, "#rfile-msg")
    kind = br.js("return document.getElementById('rfile-status').className;")
    ok = bool(msg) and "PDF" in msg and "текст" in msg.lower()
    report(ok, "ОТКАЗ по PDF ЧЕСТНЫЙ: на экране названа дорога, а не пустая кнопка",
           f"[{(msg or '')[:120]}]")
    report("err" not in kind, "отказ по PDF не покрашен как поломка",
           f"класс строки состояния: {kind}")


def run_ui_circle_pdf(br, report, note, samples: dict):
    """ШАГ, КОТОРЫЙ ПОЙМАЛ БЫ ВЧЕРАШНИЙ ДЕФЕКТ №2.

    PDF со страницей без текстового слоя. Предупреждение лежало в ответе сервера
    (поле pdf_issues) и круг считал его показанным — а на ЭКРАНЕ его не было
    НИКОГДА. Здесь спрашивается ровно то, что видит человек: плашка отрисована,
    не скрыта, имеет размер и называет, что именно не прочитано.
    """
    br.js("show('enc'); resetEnc(); return 'ok';")
    br.wait_js("document.getElementById('scr-enc').classList.contains('active')",
               "экран загрузки открылся снова")
    put_file(br, "#file", "scan_page.pdf", samples["pdf_scan"])
    br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
               "PDF ПРИНЯТ ИНТЕРФЕЙСОМ и дошёл до экрана проверки", timeout=300)
    report(True, "PDF ЗАГРУЖЕН ЧЕРЕЗ ЭКРАН (не через API)", "scan_page.pdf")

    warn = visible_text(br, "#v-lossy")
    report(bool(warn), "ПРЕДУПРЕЖДЕНИЕ О НЕПРОЧИТАННОЙ СТРАНИЦЕ PDF ВИДНО НА ЭКРАНЕ",
           (warn or "").replace("\n", " ")[:110] if warn
           else "плашки на экране НЕТ — это ровно вчерашний дефект")
    has_page = bool(warn) and re.search(r"страниц", warn, re.I) is not None
    report(has_page, "предупреждение НАЗЫВАЕТ, что именно не прочитано",
           (warn or "").replace("\n", " ")[:110])

    in_answer = br.js("return !!((typeof CUR!=='undefined' && CUR ? CUR.pdf_issues : [])||[]).length;")
    report(in_answer, "то же предупреждение есть и в ответе программы (согласованность)",
           "поле pdf_issues непустое")
    return br.js("return (typeof CUR!=='undefined' && CUR ? CUR.session_id : '')||'';")
