"""Путь к каталогу с ДАННЫМИ приложения (entity_types.yaml, app/index.html).

В разработке это корень репозитория; в собранном PyInstaller'ом (--onedir)
виде — `sys._MEIPASS` (для этой сборки — папка `_internal` рядом с exe, куда
`packaging/shifrator.spec` кладёт datas). `__file__`-арифметика ("два уровня
вверх от этого модуля"), которой пользовались core.py/server.py в
разработке, ломается в frozen-сборке: `__file__` фреймового модуля указывает
на путь ВНУТРИ `_internal`, а не на исходное дерево репозитория — оба случая
сведены в одну функцию, чтобы не решать эту загадку в каждом модуле заново.
"""

import os
import sys


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
