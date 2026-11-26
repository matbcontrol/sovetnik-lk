"""Сколько знаков в текстовых разделах формы (лимит формы — 10 000 вместе с заголовками)."""
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
text = io.open(Path(__file__).with_name("form.md"), encoding="utf-8").read()
parts = text.split("\n## ")[2:]                      # разделы после «Ссылки»
total = 0
for p in parts:
    body = "## " + p.strip()
    total += len(body)
    print(f"{len(body):6}  {body.splitlines()[0]}")
print(f"{total:6}  всего (лимит 10 000)")
