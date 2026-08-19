"""ЭТАП UI-FIT-2 — замер двух правок экрана проверки (разведка, не гейт).

Правка 1: блок-подсказка «Проверьте глазами» не растёт с окном — её высота
одинакова на любом размере, вся свободная высота уходит панели с текстом.

Правка 2: у прокручиваемого тела договора есть запас сверху и снизу, чтобы
ЛЮБАЯ строка, включая первую и последнюю, вставала ровно на полосу AI vision;
и разметка мышью в обоих крайних положениях по-прежнему даёт верные границы.

Запуск: venv/Scripts/python.exe experiments/ui_fit2_probe.py
"""

import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import server as server_mod  # noqa: E402
import ui_circle  # noqa: E402

# Размеры из задания. Берём их как ВИДИМУЮ область — это строже, чем размер
# окна: обвязка браузера съела бы ещё ~37 px высоты.
VIEWPORTS = [
    (1024, 600, "1024x600"),
    (1366, 768, "1366x768"),
    (1920, 1080, "1920x1080"),
]

# --- Правка 1: высота подсказки и что достаётся тексту ---------------------- #
HINT_JS = r"""
const pick = s => document.querySelector(s);
const box = (s) => { const el = pick(s); if(!el) return null;
  const r = el.getBoundingClientRect(), st = getComputedStyle(el);
  return {h: Math.round(r.height), w: Math.round(r.width), flex: st.flex,
          maxW: st.maxWidth}; };
const note = pick('#v-eyes .note');
const vis = s => { const el = pick(s); if(!el) return false;
  const st = getComputedStyle(el);
  return st.display !== 'none' && st.visibility !== 'hidden'; };
return JSON.stringify({
  eyes: box('#v-eyes'), banners: box('.v-banners'),
  grid: box('.verify-grid'), body: box('#pane-anon'),
  noteFs: note ? getComputedStyle(note).fontSize : '-',
  noteW: note ? Math.round(note.getBoundingClientRect().width) : 0,
  h3Fs: pick('#v-eyes h3') ? getComputedStyle(pick('#v-eyes h3')).fontSize : '-',
  свёрнут: !vis('#v-eyes'), строкаВидна: vis('.banner-slim'),
  крестикВиден: vis('.eyes-x'), строкаПроНабор: vis('#v-policy')
});
"""

# --- Правка 2: достанет ли первая/последняя строка до полосы ---------------- #
# firstGap — насколько ВЕРХ первой строки отстоит от ВЕРХНЕЙ кромки полосы,
# когда документ уведён в самый верх. 0 = строка встала ровно на полосу,
# положительное = не дотянулась (осталась ниже полосы).
# lastGap — то же для последней строки, когда документ уведён в самый низ:
# 0 = встала на полосу, отрицательное = ушла выше, не дотянувшись обратно.
REACH_JS = r"""
const body = document.getElementById('pane-anon');
const band = document.getElementById('scan-band');
if(!body||!band) return JSON.stringify({err:'нет узла'});
const rows = body.querySelectorAll('.ln-row');
if(!rows.length) return JSON.stringify({err:'нет строк'});
const first = rows[0], last = rows[rows.length-1];
const st = getComputedStyle(body);
const bodyRect = () => body.getBoundingClientRect();
const bandRect = () => band.getBoundingClientRect();
const out = {padTop: st.paddingTop, padBot: st.paddingBottom,
             scrollH: body.scrollHeight, clientH: body.clientHeight,
             lines: rows.length,
             bandTop: Math.round(bandRect().top - bodyRect().top),
             bandBot: Math.round(bodyRect().bottom - bandRect().bottom),
             maxScroll: body.scrollHeight - body.clientHeight};
body.scrollTop = 0;
out.firstGap = Math.round(first.getBoundingClientRect().top - bandRect().top);
body.scrollTop = body.scrollHeight;
out.lastGap = Math.round(last.getBoundingClientRect().top - bandRect().top);
// Прямая подводка: доехать до полосы и посмотреть, доехали ли. Именно это
// и требуется — «строка может ВСТАТЬ на полосу», а не «упирается в край».
function park(row){
  body.scrollTop = 0;
  for (let i = 0; i < 4; i++) {
    const d = row.getBoundingClientRect().top - bandRect().top;
    if (Math.abs(d) <= 1) break;
    body.scrollTop += d;
  }
  const br = bandRect(), rr = row.getBoundingClientRect();
  return {off: Math.round(rr.top - br.top),
          onBand: rr.top + 2 >= br.top && rr.top + 2 < br.bottom};
}
out.parkFirst = park(first);
out.parkLast = park(last);
body.scrollTop = 0;
return JSON.stringify(out);
"""

# --- Правка 2, вторая половина: разметка мышью в крайних положениях --------- #
# Настоящий Range на непомеченном слове КРАЙНЕЙ строки + настоящий mouseup:
# работает ровно тот путь, что у протяжки мышью (index.html::fireSelection).
SELECT_EDGE_JS = r"""
const body = document.getElementById('pane-anon');
const rows = body.querySelectorAll('.ln-row');
if(!rows.length) return JSON.stringify({err:'нет строк'});
const atTop = POS === 'top';
body.scrollTop = atTop ? 0 : body.scrollHeight;
const row = atTop ? rows[0] : rows[rows.length-1];
let node=null, from=0, to=0;
for(const lit of row.querySelectorAll('span.lit')){
  const t = lit.firstChild;
  if(!t || t.nodeType!==3) continue;
  const m = /[A-Za-zА-Яа-яЁё]{5,}/.exec(t.nodeValue||'');
  if(!m) continue;
  node=t; from=m.index; to=m.index+m[0].length; break;
}
if(!node) return JSON.stringify({err:'в крайней строке нет непомеченного слова'});
const lit = node.parentElement;
const r=document.createRange(); r.setStart(node, from); r.setEnd(node, to);
const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
const box=lit.getBoundingClientRect();
document.dispatchEvent(new MouseEvent('mouseup',
  {bubbles:true, clientX:Math.round(box.left+2), clientY:Math.round(box.top+2)}));
return JSON.stringify({
  text: node.nodeValue.slice(from,to),
  line: row.dataset.line||'?',
  seg: lit.dataset.seg,
  // ОЖИДАЕМЫЕ границы в координатах исходного сегмента
  start: (+lit.dataset.s)+from, end: (+lit.dataset.s)+to,
  onBand: (function(){
    const br=document.getElementById('scan-band').getBoundingClientRect();
    const rr=row.getBoundingClientRect();
    return rr.top+2>=br.top && rr.top+2<br.bottom;
  })()
});
"""

MENU_JS = r"""
const m=document.getElementById('markup-menu');
const t=m.querySelector('.mm-title');
return JSON.stringify({open: m.style.display==='block',
                       title: (t?t.textContent:'').slice(0,120)});
"""


def sample_text():
    """Синтетический договор: реальные документы не коммитятся (запрет 7)."""
    head = [
        "ПЕРВАЯ СТРОКА ДОГОВОРА, она обязана доставать до полосы сканирования",
        "г. Вологда, 14 марта 2024 года",
        "",
        "Общество с ограниченной ответственностью «Ромашка», ИНН 7736050003, "
        "в лице директора Петрова Петра Петровича, именуемое Исполнитель, и "
        "гражданка Сидорова Анна Викторовна, паспорт 19 04 556677, адрес: "
        "160000, Вологодская обл., г. Вологда, ул. Ленина, д. 12, кв. 5, "
        "телефон +7 921 555-33-11, заключили настоящий договор.",
        "",
    ]
    mid = [
        f"{i}. Расчёты ведутся по счёту 40702810{i:012d} в банке, "
        f"БИК 044525225, сумма {i * 1000} рублей."
        for i in range(1, 40)
    ]
    tail = ["", "ПОСЛЕДНЯЯ СТРОКА ДОГОВОРА, она тоже обязана доставать до полосы"]
    return chr(10).join(head + mid + tail)


def verdict(gap, tol=6):
    if abs(gap) <= tol:
        return "ВСТАЁТ на полосу"
    return "НЕ ДОСТАЁТ" if gap > 0 else "проскочила выше"


def main():
    exe = ui_circle.find_browser()
    if exe is None:
        raise SystemExit("не найден ни Edge, ни Chrome — замерять нечем")

    port = server_mod.find_free_port(server_mod.DEFAULT_PORT)
    httpd = server_mod.Server((server_mod.HOST, port), server_mod.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{server_mod.HOST}:{port}/"
    print(f"сервер: {url}")

    profile = os.path.join(ROOT, "experiments", ".layout-profile")
    br = ui_circle.Browser(exe, profile)
    try:
        br.navigate(url)
        time.sleep(1.5)
        ui_circle.put_file(br, "#file", "probe_contract.txt",
                           sample_text().encode("utf-8"))
        br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
                   "экран проверки открылся", timeout=300)

        print("\n##### ПРАВКА 1 — высота подсказки «Проверьте глазами» #####")
        # Три размера из задания + три РАВНОЙ ВЫСОТЫ и разной ширины: только
        # так видно, растёт ли блок от ширины окна. На высоте ниже 820 px
        # полный баннер по замыслу дизайна свёрнут в строку (правило
        # @media max-height:819px), и сравнивать там нечего.
        for w, h, name in VIEWPORTS + [(1440, 1080, "1440x1080"),
                                       (1680, 1080, "1680x1080")]:
            br.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.7)
            d = json.loads(br.js(HINT_JS))
            b, g, bd = d["banners"], d["grid"], d["body"]
            print(f"\n=== {name} ===")
            print(f"  ПО УМОЛЧАНИЮ: свёрнут={d['свёрнут']},"
                  f" строка-напоминание видна={d['строкаВидна']},"
                  f" строка про набор типов видна={d['строкаПроНабор']}")
            print(f"  .v-banners   высота {b['h']:>5} px  flex={b['flex']}")
            print(f"  .verify-grid высота {g['h']:>5} px  <- место текста договора")
            print(f"  #pane-anon   высота {bd['h']:>5} px")
            # развернуть -> измерить -> свернуть обратно крестиком
            br.js("if(!document.documentElement.classList.contains('eyes-open'))"
                  " toggleEyesBanner();")
            time.sleep(0.5)
            o = json.loads(br.js(HINT_JS))
            e = o["eyes"]
            print(f"  РАЗВЁРНУТ: #v-eyes высота {e['h']:>5} px  ширина {e['w']:>5} px"
                  f"  flex={e['flex']}")
            print(f"    текст: кегль {o['noteFs']}, заголовок {o['h3Fs']},"
                  f" ширина строки чтения {o['noteW']} px;"
                  f" ✕ виден={o['крестикВиден']}")
            print(f"    .v-banners {o['banners']['h']} px,"
                  f" .verify-grid {o['grid']['h']} px"
                  f"  (у текста отнято {g['h']-o['grid']['h']} px)")
            br.js("document.querySelector('.eyes-x').click();")
            time.sleep(0.5)
            c = json.loads(br.js(HINT_JS))
            print(f"  ПОСЛЕ ЩЕЛЧКА ПО ✕: свёрнут={c['свёрнут']},"
                  f" .verify-grid {c['grid']['h']} px")

        print("\n##### ПРАВКА 2 — запас прокрутки под полосу AI vision #####")
        br.js("if(!SCAN.on) toggleAiVision();")
        time.sleep(1.0)
        for w, h, name in VIEWPORTS:
            br.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.9)
            br.js("scanApply(0)")
            time.sleep(0.6)
            d = json.loads(br.js(REACH_JS))
            print(f"\n=== {name} ===")
            if "err" in d:
                print("  " + d["err"])
                continue
            print(f"  полоса: от верха панели {d['bandTop']} px,"
                  f" от полосы до низа панели {d['bandBot']} px")
            print(f"  запас CSS: сверху {d['padTop']}, снизу {d['padBot']}")
            print(f"  прокрутка: видно {d['clientH']} из {d['scrollH']} px,"
                  f" ход {d['maxScroll']} px, строк {d['lines']}")
            print(f"  край прокрутки: первая строка в самом верху {d['firstGap']:+} px"
                  f" от кромки полосы, последняя в самом низу {d['lastGap']:+} px")
            for who, p in (("ПЕРВАЯ", d["parkFirst"]), ("ПОСЛЕДНЯЯ", d["parkLast"])):
                print(f"  {who:<9} строка, подведена к полосе: {p['off']:+} px"
                      f" от кромки -> {'ВСТАЛА НА ПОЛОСУ' if p['onBand'] else 'НЕ ДОСТАЁТ'}")

        print("\n##### ПРАВКА 2 — разметка мышью в крайних положениях #####")
        br.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.7)
        for pos, human in (("top", "документ в самом верху"),
                           ("bot", "документ в самом низу")):
            got = json.loads(br.js(SELECT_EDGE_JS.replace("POS", json.dumps(pos))))
            if "err" in got:
                print(f"  {human}: {got['err']}")
                continue
            time.sleep(0.8)
            menu = json.loads(br.js(MENU_JS))
            ok = menu["open"] and got["text"] in menu["title"]
            print(f"\n  {human}: выделено «{got['text']}» (строка {got['line']},"
                  f" сегмент {got['seg']}, границы [{got['start']}:{got['end']}],"
                  f" строка {'НА полосе' if got['onBand'] else 'вне полосы'})")
            print(f"    плашка типов: {'ОТКРЫЛАСЬ' if menu['open'] else 'НЕ ОТКРЫЛАСЬ'};"
                  f" фрагмент в заголовке {'СОВПАЛ' if ok else 'РАЗОШЁЛСЯ'}"
                  f" -> {menu['title']!r}")
            br.js("closeMarkupMenu(); clearSelection();")
            time.sleep(0.3)

        print("\n##### КАРТА РИСКА ВКЛЮЧЕНА ПО УМОЛЧАНИЮ #####")
        # Свежая загрузка страницы: состояние карты не должно зависеть от того,
        # что делали в этой сессии выше.
        br.navigate(url)
        time.sleep(1.5)
        ui_circle.put_file(br, "#file", "probe_contract.txt",
                           sample_text().encode("utf-8"))
        br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
                   "экран проверки открылся", timeout=300)
        time.sleep(0.6)
        state = br.js(
            "const b=document.getElementById('v-riskmap'),"
            "      c=document.getElementById('ctl-riskmap'),"
            "      t=document.getElementById('v-riskmap-track');"
            "return JSON.stringify({видна: !b.classList.contains('hidden'),"
            " кнопка_нажата: c.classList.contains('active'),"
            " ширина: Math.round(b.getBoundingClientRect().width),"
            " нарисована: !!t.querySelector('svg'),"
            " столбцов: t.querySelectorAll('.riskmap-cols>div').length});")
        print("  при открытии экрана: " + state)
        br.js("toggleRiskMap()")
        time.sleep(0.4)
        print("  после щелчка по кнопке: " + br.js(
            "const b=document.getElementById('v-riskmap'),"
            "      c=document.getElementById('ctl-riskmap');"
            "return JSON.stringify({видна: !b.classList.contains('hidden'),"
            " кнопка_нажата: c.classList.contains('active')});"))

        print("\n##### ПЛАШКА В ОПИСИ = ПЛАШКА В ТЕКСТЕ, обе темы #####")
        # Сличаем ВЫЧИСЛЕННЫЕ стили одного и того же типа: маска в тексте
        # против плашки в описи. Расхождение по любому свойству — беда.
        br.cmd("Emulation.setDeviceMetricsOverride",
               {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)
        for theme in ("depth", "glass"):
            br.js(f"setMarksTheme({json.dumps(theme)})")
            time.sleep(0.5)
            out = json.loads(br.js(r"""
const PROPS=['backgroundColor','color','borderTopWidth','borderTopColor',
             'borderTopStyle','borderBottomStyle','borderRadius','fontFamily',
             'fontSize','fontWeight','letterSpacing','paddingTop','paddingLeft',
             'boxShadow','lineHeight'];
const rows=[];
const seen=new Set();
for(const tok of document.querySelectorAll('#v-list mark.tok')){
  const t=[...tok.classList].find(c=>c.startsWith('t-'));
  if(!t || seen.has(t)) continue;
  const inText=document.querySelector('#pane-anon mark.ent.'+t);
  if(!inText) continue;
  seen.add(t);
  const a=getComputedStyle(inText), b=getComputedStyle(tok);
  const diff=PROPS.filter(p=>a[p]!==b[p]).map(p=>p+': текст '+a[p]+' / опись '+b[p]);
  const supA=getComputedStyle(inText,'::after').content;
  const supB=getComputedStyle(tok,'::after').content;
  if(supA!==supB) diff.push('::after: текст '+supA+' / опись '+supB);
  rows.push({тип:t, расхождений:diff.length, что:diff.slice(0,4)});
}
return JSON.stringify(rows);
"""))
            bad = [r for r in out if r["расхождений"]]
            print(f"  тема «{theme}»: сличено типов {len(out)},"
                  f" расходятся {len(bad)}")
            for r in bad:
                print(f"      {r['тип']}: " + "; ".join(r["что"]))
        br.js("setMarksTheme('depth')")
    finally:
        br.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
