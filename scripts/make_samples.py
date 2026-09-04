"""Собирает удобный набор примеров для ручной проверки через интерфейс.

Запуск:  python scripts/make_samples.py

Из выгруженного репозитория примеров кейса берутся только условия и
решения — по одному набору на курс, плоскими файлами с говорящими именами.
Репозитории, каталоги mlruns и прочий вспомогательный мусор не переносятся:
через форму загрузки их всё равно не выбрать.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC = Path("data/examples")
DST = Path("data/примеры_домашек")

# Расширения, которые имеет смысл грузить через форму.
KEEP = {".docx", ".pdf", ".xlsx", ".xlsm", ".md", ".txt", ".ipynb", ".zip"}
SKIP_DIRS = {"mlruns", ".git", "__pycache__", "node_modules", ".idea"}


def label(path: Path) -> str:
    """Имя файла: курс, задание, уровень решения — всё в одной строке."""
    rel = path.relative_to(SRC)
    # Первый сегмент — имя курса, и оно же имя каталога назначения.
    # Дублировать его в имени файла незачем.
    parts = [p for p in rel.parts[1:-1] if p not in SKIP_DIRS]
    stem = rel.stem
    name = " — ".join([*parts, stem]) if parts else stem
    # Windows не любит эти символы в именах.
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "-")
    return name[:150] + path.suffix.lower()


def main() -> int:
    if DST.exists():
        shutil.rmtree(DST)
    copied = 0
    per_course: dict[str, int] = {}

    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in KEEP:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(SRC).parts):
            continue
        # Внутри распакованных репозиториев лежат сотни .md и .txt —
        # это исходники решения, а не сдаваемый документ.
        rel_parts = path.relative_to(SRC).parts
        if any(part.startswith("course-") or part.endswith("-master") for part in rel_parts):
            continue

        course = rel_parts[0]
        out_dir = DST / course
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / label(path)
        if target.exists():
            target = out_dir / f"{target.stem}_{copied}{target.suffix}"
        shutil.copy2(path, target)
        copied += 1
        per_course[course] = per_course.get(course, 0) + 1

    for course, n in sorted(per_course.items()):
        print(f"  {course:28} {n} файлов")
    print(f"\nвсего скопировано: {copied} в {DST}")
    return 0 if copied else 1


if __name__ == "__main__":
    raise SystemExit(main())
