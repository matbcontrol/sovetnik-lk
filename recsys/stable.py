"""Воспроизводимость расчётов.

Порядок обхода множеств строк в Python по умолчанию меняется от запуска к запуску. От него зависит
порядок строк обучения и разрешение ничьих, поэтому итоговая цифра «гуляла» на несколько десятых
процентного пункта. Чтобы отчёт давал одно и то же число при каждом запуске, расчётные модули
перезапускают себя с постоянным порядком (PYTHONHASHSEED=0).
"""
from __future__ import annotations

import os
import subprocess
import sys


def rerun_with_fixed_order(module: str) -> None:
    """Если порядок обхода не зафиксирован — перезапускает модуль с PYTHONHASHSEED=0 и завершает процесс."""
    if os.environ.get("PYTHONHASHSEED") == "0":
        return
    env = dict(os.environ, PYTHONHASHSEED="0")
    sys.exit(subprocess.run([sys.executable, "-m", module, *sys.argv[1:]], env=env).returncode)
