"""Сборка PDF с документацией проекта.

Markdown → HTML делает `python-markdown`, HTML → PDF — Chromium через
Playwright (`web/scripts/make_pdf.mjs`). Обе части офлайн.

Порядок документов не алфавитный, а такой, в каком их читают: сначала
обязательный артефакт сдачи, затем обзор, затем разборы по темам.

    python scripts/build_docs_pdf.py
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "pdf"

# Первый элемент пары — файл, второй — название раздела в оглавлении.
DOCUMENTS: list[tuple[str, str]] = [
    ("PROJECT.md", "Описание проекта"),
    ("README.md", "Установка, запуск и состав"),
    # Руководство идёт третьим: после «что это» и «как поставить» человеку
    # нужно «как этим пользоваться», а не архитектура.
    ("docs/guide.md", "Руководство пользователя и сценарий показа"),
    ("docs/add-homework.md", "Как добавить своё домашнее задание"),
    ("docs/as-is-to-be.md", "Текущий и целевой процесс"),
    ("docs/architecture.md", "Архитектура"),
    ("docs/agent_instructions.md", "Инструкции агента"),
    ("docs/security.md", "Защита данных и обработка ошибок моделей"),
    ("docs/metrics.md", "Метрики качества и эффекта"),
    ("docs/cost.md", "Стоимость эксплуатации"),
    ("docs/scalability.md", "Масштабирование"),
    ("docs/configuration.md", "Конфигурация"),
    ("docs/limitations.md", "Честные ограничения"),
    ("docs/demo_script.md", "Сценарий демонстрации"),
]

CSS = """
:root { --ink:#10151c; --dim:#5b6675; --line:#dfe3e8; --brand:#0069a8; --wash:#f5f7f9; }
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink); background: #fff;
  font: 10pt/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3, h4 { line-height: 1.25; margin: 0 0 .4em; page-break-after: avoid; }
h1 { font-size: 20pt; }
h2 { font-size: 14pt; margin-top: 1.6em; padding-bottom: .25em; border-bottom: 1px solid var(--line); }
h3 { font-size: 11.5pt; margin-top: 1.3em; }
h4 { font-size: 10pt; margin-top: 1.1em; color: var(--dim); }
p, ul, ol, blockquote, table { margin: 0 0 .7em; }
ul, ol { padding-left: 1.3em; }
li { margin-bottom: .18em; }
a { color: var(--brand); text-decoration: none; }
code {
  font: 9pt/1.4 "Cascadia Mono", Consolas, monospace;
  background: var(--wash); padding: .1em .3em; border-radius: 3px;
  overflow-wrap: anywhere;
}
pre {
  background: var(--wash); border: 1px solid var(--line); border-radius: 5px;
  padding: .7em .9em; overflow-x: auto; page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8.5pt; white-space: pre; }
blockquote {
  margin-left: 0; padding: .5em .9em; border-left: 3px solid var(--brand);
  background: var(--wash); color: var(--dim);
}
blockquote p:last-child { margin-bottom: 0; }
table { width: 100%; border-collapse: collapse; font-size: 8.8pt; page-break-inside: auto; }
th, td { border: 1px solid var(--line); padding: .35em .5em; text-align: left; vertical-align: top; }
th { background: var(--wash); font-weight: 600; }
tr { page-break-inside: avoid; }
hr { border: 0; border-top: 1px solid var(--line); margin: 1.4em 0; }
img { max-width: 100%; }

/* ── титул и оглавление ─────────────────────────────────────────────── */
.cover { height: 235mm; display: flex; flex-direction: column; justify-content: center; }
.cover .kicker { color: var(--brand); font-weight: 600; letter-spacing: .08em; text-transform: uppercase; font-size: 9pt; }
.cover h1 { font-size: 30pt; margin: .3em 0 .2em; }
.cover .sub { font-size: 12pt; color: var(--dim); max-width: 130mm; }
.cover .meta { margin-top: 2.2em; font-size: 9.5pt; color: var(--dim); }
.cover .meta b { color: var(--ink); font-weight: 600; }
.cover .facts { margin-top: 2.4em; display: grid; grid-template-columns: repeat(2, 1fr); gap: .6em 1.4em; max-width: 150mm; }
.cover .facts div { border-left: 2px solid var(--line); padding-left: .7em; font-size: 9pt; color: var(--dim); }
.cover .facts b { display: block; color: var(--ink); font-size: 13pt; }
.toc { page-break-before: always; }
.toc ol { list-style: none; padding: 0; counter-reset: doc; }
.toc li { counter-increment: doc; padding: .35em 0; border-bottom: 1px dotted var(--line); font-size: 10.5pt; }
.toc li::before { content: counter(doc) ". "; color: var(--dim); }
.doc { page-break-before: always; }
.doc > h1 { padding-bottom: .3em; border-bottom: 2px solid var(--brand); }
.doc .source { font-size: 8.5pt; color: var(--dim); margin: -0.2em 0 1.4em; }
"""


def render_markdown(text: str) -> str:
    """Markdown → HTML. Ссылки на локальные `.md` обезвреживаются."""
    import markdown

    # Внутренние ссылки в PDF никуда не ведут: превращаем их в текст, иначе
    # читатель кликает по «docs/metrics.md» и попадает в никуда.
    text = re.sub(r"\[([^\]]+)\]\((?!https?:)[^)]+\)", r"\1", text)

    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html",
    )


def strip_first_heading(body: str) -> tuple[str, str]:
    """Вынимает первый `<h1>`: он становится заголовком раздела."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    if not m:
        return "", body
    return re.sub(r"<[^>]+>", "", m.group(1)).strip(), body[: m.start()] + body[m.end() :]


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    toc: list[str] = []
    for path, section in DOCUMENTS:
        src = ROOT / path
        if not src.exists():
            print(f"  ! нет файла {path}")
            continue
        title, body = strip_first_heading(render_markdown(src.read_text(encoding="utf-8")))
        heading = section or title
        toc.append(f"<li>{html.escape(heading)}</li>")
        parts.append(
            f'<section class="doc"><h1>{html.escape(heading)}</h1>'
            f'<div class="source">{html.escape(path)}</div>{body}</section>'
        )

    facts = [
        ("121 тест", "проходят за 7 секунд"),
        ("0 внешних вызовов", "в рантайме, кроме локальной модели"),
        ("63 файла примеров", "из репозитория кейса читаются без ошибок"),
        ("45 секунд", "предварительное ревью одной работы"),
    ]
    facts_html = "".join(
        f"<div><b>{html.escape(a)}</b>{html.escape(b)}</div>" for a, b in facts
    )

    doc = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Avito AI Reviewer — документация</title>
<style>{CSS}</style></head>
<body>
<section class="cover">
  <div class="kicker">Хакатон Avito · кейс «AI Reviewer»</div>
  <h1>Avito AI Reviewer</h1>
  <div class="sub">Помощник ревьюера и координатора образовательных программ.
  Полная документация решения: процесс, архитектура, инструкции агента,
  защита данных, метрики, ограничения.</div>
  <div class="meta">
    <b>Локальный контур.</b> Единственный внешний адрес в рантайме —
    модель на <code>localhost</code>. Персональные данные не покидают машину.<br>
    <b>Итоговое решение — за человеком.</b> Балл предварительный и публикуется
    студенту только после подтверждения ревьюером.
  </div>
  <div class="facts">{facts_html}</div>
</section>

<section class="toc">
  <h1>Содержание</h1>
  <ol>{"".join(toc)}</ol>
</section>

{"".join(parts)}
</body></html>"""

    html_path = OUT_DIR / "documentation.html"
    html_path.write_text(doc, encoding="utf-8")
    return html_path


def to_pdf(html_path: Path, pdf_path: Path, running_title: str) -> None:
    """Печать через Chromium. Node нужен только на этом шаге, не на демо."""
    # Именно `node`, а не `npx node`: npx не проксирует сам интерпретатор
    # и выходит с ошибкой. Рабочий каталог — `web/`, потому что ESM ищет
    # playwright относительно файла скрипта.
    node = "node.exe" if sys.platform == "win32" else "node"
    subprocess.run(
        [node, "scripts/make_pdf.mjs", str(html_path), str(pdf_path), running_title],
        cwd=ROOT / "web",
        check=True,
    )


if __name__ == "__main__":
    page = build()
    print(f"  {page}")
    pdf = OUT_DIR / "Avito_AI_Reviewer_документация.pdf"
    to_pdf(page, pdf, "Документация")
    print(f"\nГотово: {pdf.relative_to(ROOT)}  ({pdf.stat().st_size // 1024} КБ)")
