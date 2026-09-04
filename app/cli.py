"""CLI: ревью одной работы без запуска веб-приложения.

Нужен на этапе, когда интерфейса ещё нет, и остаётся полезным потом —
для отладки и для прогона бенчмарка.

    python -m app.cli review <работа.docx> --condition <условие.pdf>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agent.pipeline import run_review
from app.agent.schemas import EvidenceStatus
from app.ingest.loader import read_any
from app.rubric.extract import extract_rubric
from app.rubric.models import Rubric

C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m",
}


def _c(text: str, color: str) -> str:
    return f"{C[color]}{text}{C['reset']}"


def _load_rubric(
    condition_path: Path, cache: Path | None, *, fast: bool, track: str
) -> Rubric:
    """Рубрика из кэша либо извлечение заново.

    Кэш ключуется отпечатком промпта извлечения и самого файла условия —
    то же правило, что в бенчмарке. Фиксированное имя файла кэша давало
    два отказа сразу: рубрика одного курса подставлялась другому, а после
    правки промптов CLI молча считал по старой конфигурации.

    Неудачное извлечение НЕ кэшируется. Иначе одна недоступность модели
    отравляла кэш навсегда: следующий запуск читал пустую рубрику из файла,
    не обращался к модели вовсе и печатал ту же ошибку — уже при живой
    модели. Ровно это и наблюдалось.
    """
    import hashlib

    from app.rubric import extract as extract_mod

    condition = read_any(condition_path)
    if cache is not None:
        fingerprint = hashlib.sha256(
            (
                extract_mod._SYSTEM
                + track
                + condition_path.read_bytes().hex()[:64]
            ).encode()
        ).hexdigest()[:12]
        cache = cache.parent / f"{cache.stem}_{fingerprint}{cache.suffix}"

    if cache and cache.exists():
        rubric = Rubric.model_validate_json(cache.read_text(encoding="utf-8"))
        print(_c(f"рубрика из кэша: {cache}", "dim"))
        return rubric

    rubric = extract_rubric(condition, track=track, fast=fast)
    if cache and rubric.criteria:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(rubric.model_dump_json(indent=2), encoding="utf-8")
    return rubric


def cmd_review(args: argparse.Namespace) -> int:
    work_path = Path(args.work)
    condition_path = Path(args.condition) if args.condition else None

    print(_c(f"\n{'=' * 78}", "dim"))
    print(_c(f"РАБОТА: {work_path.name}", "bold"))
    print(_c("=" * 78, "dim"))

    work = read_any(work_path)
    print(f"формат {work.source_format}, блоков {len(work.blocks)}, "
          f"символов {work.char_count}, таблиц {work.facts.get('table_count', 0)}")

    if not condition_path:
        print(_c("Условие не передано — нужен --condition для извлечения рубрики.", "red"))
        return 2

    cache = None if args.no_cache else (Path(args.rubric_cache) if args.rubric_cache else None)
    rubric = _load_rubric(condition_path, cache, fast=args.fast, track=args.track)
    if not rubric.criteria:
        print(_c(f"Рубрика пуста. {rubric.notes}", "red"))
        return 2

    # В CLI рубрика утверждается автоматически: интерактивного методиста здесь
    # нет. В приложении утверждение выполняет человек, и это обязательный шаг.
    rubric.approve(by="cli")
    print(f"рубрика: {len(rubric.criteria)} критериев, максимум {rubric.total_max:g}")
    if rubric.notes:
        print(_c(f"  замечания: {rubric.notes}", "yellow"))

    condition = read_any(condition_path)

    def progress(stage: str, fraction: float) -> None:
        print(_c(f"  [{fraction:5.0%}] {stage}", "dim"), flush=True)

    result = run_review(
        work, rubric, condition=condition,
        submission_id=work_path.stem, fast=args.fast, progress=progress,
    )

    _print_result(result)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(_c(f"\nJSON сохранён: {out}", "dim"))
    return 0


def _print_result(result) -> None:  # noqa: ANN001
    print(_c("\n── ФОРМАЛЬНЫЕ ПРОВЕРКИ ─────────────────────────────────────────", "bold"))
    icons = {"pass": _c("✔", "green"), "fail": _c("✘", "red"),
             "unknown": _c("?", "yellow"), "na": _c("—", "dim")}
    for f in result.formal:
        print(f"  {icons.get(f['status'], '?')} {f['title']:32} {f['message'][:80]}")

    print(_c("\n── КРИТЕРИИ ────────────────────────────────────────────────────", "bold"))
    for c in result.criteria:
        if c.failed:
            print(f"  {_c('✘', 'red')} {c.criterion_name}: {_c('не оценён', 'red')} — {c.error[:60]}")
            continue
        share = c.score / c.max_score if c.max_score else 0
        color = "green" if share >= 0.75 else "yellow" if share >= 0.4 else "red"
        flag = _c(" [цитаты не подтверждены]", "red") if c.unverified else ""
        print(f"  {_c(f'{c.score:g}/{c.max_score:g}', color)}  {c.criterion_name}{flag}")
        print(f"       {c.verdict[:110]}")
        for ev in c.evidence:
            mark = {
                EvidenceStatus.VERIFIED: _c("✔", "green"),
                EvidenceStatus.WRONG_BLOCK: _c("~", "yellow"),
            }.get(ev.status, _c("✘", "red"))
            print(f"       {mark} §{ev.block} ({ev.similarity:g}%) «{ev.quote[:70]}»")
        for g in c.gaps[:3]:
            print(_c(f"       · пробел: {g[:100]}", "dim"))

    if result.ai_signal:
        s = result.ai_signal
        print(_c("\n── ПРИЗНАКИ ГЕНЕРАТИВНОГО ИИ ───────────────────────────────────", "bold"))
        print(f"  скор {_c(f'{s['score']:.2f}', 'magenta')}, уверенность: {s['confidence']}")
        for sig in s["signals"]:
            v = f"{sig['value']:.2f}" if sig["value"] is not None else _c("недоступен", "dim")
            print(f"    {sig['title']:26} {v:>12}  {sig['detail'][:56]}")
        for g in s["grounds"][:4]:
            print(_c(f"    основание: {g[:98]}", "dim"))

    print(_c("\n── ИТОГ ────────────────────────────────────────────────────────", "bold"))
    print(f"  предварительный балл: {_c(f'{result.preliminary_score:g} / {result.max_score:g}', 'bold')}")
    for p in result.penalties:
        print(_c(f"    штраф −{p['amount']:g}: {p['title']}", "red"))
    print(f"  приоритет ручной проверки: {result.priority_index:.2f}")
    for r in result.priority_reasons:
        print(_c(f"    · {r}", "dim"))

    print(_c("\n── ТРАССА ──────────────────────────────────────────────────────", "bold"))
    for s in result.trace:
        mark = _c("✔", "green") if s.ok else _c("✘", "red")
        print(f"  {mark} {s.name:32} {s.duration_ms:7.0f} мс  {s.detail[:52]}")
    print(f"  {_c('всего', 'bold')}: {result.duration_ms / 1000:.1f} с, модель {result.model}")

    if result.warnings:
        print(_c("\n── ПРЕДУПРЕЖДЕНИЯ ──────────────────────────────────────────────", "bold"))
        for w in result.warnings:
            print(_c(f"  ! {w[:110]}", "yellow"))

    if result.student_feedback:
        print(_c("\n── ОБРАТНАЯ СВЯЗЬ СТУДЕНТУ ─────────────────────────────────────", "bold"))
        print("  " + result.student_feedback.replace("\n", "\n  ")[:1400])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Avito AI Reviewer")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("review", help="предварительное ревью одной работы")
    p.add_argument("work", help="файл решения (.docx/.pdf/.xlsx)")
    p.add_argument("--condition", "-c", help="файл условия задания")
    p.add_argument("--rubric-cache", default="data/rubric_cache_cli.json",
                   help="файл кэша рубрики; к имени добавляется отпечаток условия")
    p.add_argument("--track", default="product_fraud",
                   help="направление курса: product_fraud, tech_QA, system_design …")
    p.add_argument("--no-cache", action="store_true",
                   help="извлечь рубрику заново, игнорируя кэш")
    p.add_argument("--fast", action="store_true", help="younger-модель, черновой прогон")
    p.add_argument("--json", help="сохранить результат в JSON")
    p.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
