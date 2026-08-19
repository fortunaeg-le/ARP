"""ЭТАП DESKTOP-FIT — замер раскладки на разных размерах окна (разведка).

Ищет ровно один класс дефекта: содержимое, которое НЕ ПОМЕЩАЕТСЯ и при этом
НЕ ПРОКРУЧИВАЕТСЯ, — то есть срезано и человеку недоступно. Страница у нас
принципиально не скроллится (`body{overflow-y:hidden}`), поэтому любой такой
узел = потерянная кнопка или потерянный текст.

Запуск: venv/Scripts/python.exe experiments/desktop_fit_layout_probe.py
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

# Размеры ВИДИМОЙ области (не окна): первый — реальный у владельца, замерен
# appwindow._work_area() = 1280x680, обвязка окна съедает 37 px.
VIEWPORTS = [
    (1266, 643, "окно владельца (рабочая область 1280x680)"),
    (1349, 731, "ноутбук 1366x768"),
    (1004, 563, "маленький ноутбук 1024x600"),
    (1906, 1043, "монитор 1920x1080"),
    (1266, 500, "низкое окно 500 px — предел раскладки"),
    (1266, 400, "очень низкое окно 400 px"),
]

SCREENS = [
    ("enc", "show('enc')", "1 · Обезличить"),
    ("res", "show('res')", "2 · Вернуть данные"),
    ("ana", "show('ana')", "Аналитика"),
    ("sto", "show('sto')", "Что хранится"),
    ("set", "openSettings()", "Что прятать"),
]

# Возвращает узлы, чьё содержимое срезано: не помещается и не прокручивается.
CLIPPED_JS = r"""
return (function(){
  var out = [];
  var all = document.querySelectorAll('body, body *');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    var st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    var over = el.scrollHeight - el.clientHeight;
    if (over <= 1) continue;
    var oy = st.overflowY;
    if (oy === 'auto' || oy === 'scroll') continue;   // прокрутится, всё видно
    // Родитель, который прокручивается, тоже делает содержимое достижимым —
    // иначе прибор считает бедой каждую декоративную коробку внутри скролла.
    var anc = el.parentElement, reachable = false;
    while (anc) {
      var ao = getComputedStyle(anc).overflowY;
      if (ao === 'auto' || ao === 'scroll') { reachable = true; break; }
      anc = anc.parentElement;
    }
    if (reachable) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      cls: (el.className && el.className.baseVal !== undefined
              ? el.className.baseVal : String(el.className || '')).slice(0, 60),
      lost: over, overflowY: oy,
      bottom: Math.round(r.bottom)
    });
  }
  // Оставляем только самые внешние: вложенные повторяют беду родителя.
  return JSON.stringify({
    innerH: window.innerHeight, innerW: window.innerWidth,
    pageLost: document.documentElement.scrollHeight - window.innerHeight,
    nodes: out.slice(0, 12)
  });
})()
"""


# Что реально не влезает по НИЖНЕЙ кромке: ключевые узлы главного экрана.
CHAIN_JS = r"""
return (function(){
  var sels = ['body','main','#scr-ver','#scr-ver > .card','.verify-grid','.panes',
              '.panes .pane','.pane .body','.v-banners','#v-eyes'];
  var out = [];
  for (var i=0;i<sels.length;i++){
    var el = document.querySelector(sels[i]);
    if(!el){ out.push(sels[i]+': нет'); continue; }
    var st = getComputedStyle(el), r = el.getBoundingClientRect();
    out.push(sels[i]+' h='+Math.round(r.height)+' scrollH='+el.scrollHeight
             +' flex='+st.flex+' minH='+st.minHeight+' ovY='+st.overflowY);
  }
  return out.join(' | ');
})()
"""

BOTTOM_JS = r"""
return (function(){
  var names = {'#v-count':'счётчик найденного', '#pane-anon':'обезличенный текст',
               '#v-list':'список замен', '.verify-grid':'два текста рядом',
               '#scr-ver .v-actions':'кнопки под текстами'};
  var out = [];
  for (var sel in names) {
    var el = document.querySelector(sel);
    if (!el) { out.push(names[sel] + ': НЕТ В DOM'); continue; }
    var r = el.getBoundingClientRect();
    var cut = Math.round(r.bottom - window.innerHeight);
    out.push(names[sel] + ': ' + (cut > 0 ? ('срезано снизу на ' + cut + ' px') : 'видно целиком')
             + ' (высота ' + Math.round(r.height) + ')');
  }
  return out.join('; ');
})()
"""

SAMPLE = (chr(10).join([
    "ДОГОВОР ОКАЗАНИЯ УСЛУГ № 44-К",
    "г. Вологда, 14 марта 2024 года",
    "",
    "Общество с ограниченной ответственностью «Ромашка», ИНН 7736050003, "
    "в лице директора Петрова Петра Петровича, действующего на основании устава, "
    "именуемое в дальнейшем Исполнитель, и гражданка Сидорова Анна Викторовна, "
    "паспорт 19 04 556677, зарегистрированная по адресу: 160000, Вологодская обл., "
    "г. Вологда, ул. Ленина, д. 12, кв. 5, телефон +7 921 555-33-11, "
    "адрес электронной почты a.sidorova@example.ru, именуемая в дальнейшем Заказчик, "
    "заключили настоящий договор о нижеследующем.",
    "",
] + [
    f"{i}. Стороны подтверждают, что расчёты ведутся по счёту "
    f"40702810{i:012d} в банке, БИК 044525225, сумма {i * 1000} рублей."
    for i in range(1, 40)
]))


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
        for w, h, name in VIEWPORTS:
            br.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.4)
            print(f"\n=== {name}: видимая область {w}x{h} ===")
            for key, call, human in SCREENS:
                br.js(call)
                time.sleep(0.35)
                data = json.loads(br.js(CLIPPED_JS))
                bad = data["nodes"]
                if not bad and data["pageLost"] <= 1:
                    print(f"  {human:<20} — помещается")
                    continue
                print(f"  {human:<20} — СРЕЗАНО, страница теряет {data['pageLost']} px")
                for n in bad:
                    print(f"      {n['tag']}#{n['id']}.{n['cls']}"
                          f"  теряет {n['lost']} px  (overflow-y: {n['overflowY']})")
        # --- главный экран: тот, на котором человек работает -------------- #
        # Синтетический договор (реальные документы не коммитятся — запрет 7).
        ui_circle.put_file(br, "#file", "probe_contract.txt", SAMPLE.encode("utf-8"))
        br.wait_js("document.getElementById('scr-ver').classList.contains('active')",
                   "экран проверки открылся", timeout=300)
        for w, h, name in VIEWPORTS:
            br.cmd("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.6)
            br.js("show('ver')")
            time.sleep(0.5)
            data = json.loads(br.js(CLIPPED_JS))
            bad = [n for n in data["nodes"] if "cta-ico" not in n["cls"]]
            print("\n" + f"=== ЭКРАН ПРОВЕРКИ · {name}: {w}x{h} ===")
            if w == 1266:
                print("  ЦЕПОЧКА: " + br.js(CHAIN_JS))
            if not bad and data["pageLost"] <= 1:
                print("  помещается")
            else:
                print(f"  СРЕЗАНО, страница теряет {data['pageLost']} px")
                for n in bad:
                    print(f"      {n['tag']}#{n['id']}.{n['cls']}"
                          f"  теряет {n['lost']} px  (overflow-y: {n['overflowY']})")
            print("  " + br.js(BOTTOM_JS))
    finally:
        br.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
