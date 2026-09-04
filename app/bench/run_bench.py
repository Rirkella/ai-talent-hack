"""Бенчмарк качества предварительного ревью.

Кейс требует протестировать ревью не менее чем на трёх вариантах выполнения
задания и представить результаты. Здесь считаются метрики, которые можно
показать жюри и которые не льстят системе:

* **Ранжирование.** Слабое < среднее < хорошее. Это главный показатель:
  абсолютный балл субъективен, а порядок — нет. Считается доля правильно
  упорядоченных пар.
* **Стабильность.** Тот же прогон повторяется несколько раз. При
  `temperature=0` и фиксированном seed баллы обязаны совпадать; расхождение
  означает, что воспроизводимость потеряна, и это надо знать до защиты.
* **Подтверждаемость цитат.** Доля доказательств, найденных в работе. Прямая
  мера того, насколько система склонна выдумывать.
* **Скорость.** Секунды на работу — вход в расчёт стоимости эксплуатации.

Ожидаемые уровни заданы репозиторием примеров (слабое/среднее/хорошее) и
служат опорой, а не эталонными баллами: точных оценок преподавателя у нас нет,
поэтому сравнивается порядок, а не попадание в число.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.pipeline import run_review
from app.agent.schemas import ReviewResult
from app.config import PROJECT_ROOT
from app.ingest.loader import read_any
from app.rubric.extract import extract_rubric
from app.rubric.models import Rubric

EXAMPLES = PROJECT_ROOT / "data" / "examples" / "product_fraud"
CONDITION = EXAMPLES / "Product_Fraud_ДЗ2_условия.pdf"

# Порядок важен: индекс = ожидаемый уровень качества по возрастанию.
CASES: list[tuple[str, Path]] = [
    ("слабое", EXAMPLES / "Product_Fraud_ДЗ2_Решение слабое.docx"),
    ("среднее", EXAMPLES / "Product_Fraud_ДЗ2_Решение среднее.docx"),
    ("хорошее", EXAMPLES / "Product_Fraud_ДЗ2_Решение хорошее.docx"),
]


@dataclass
class CaseRun:
    label: str
    run_index: int
    score: float
    max_score: float
    duration_s: float
    evidence_total: int
    evidence_verified: int
    failed_steps: list[str] = field(default_factory=list)
    failed_criteria: int = 0
    ai_score: float = 0.0
    ai_confidence: str = ""
    formal_violations: int = 0
    per_criterion: dict[str, float] = field(default_factory=dict)


@dataclass
class BenchReport:
    runs: list[CaseRun] = field(default_factory=list)
    model: str = ""
    started_at: str = ""

    # ── агрегаты ──────────────────────────────────────────────────────────

    def scores_by_label(self) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for r in self.runs:
            out.setdefault(r.label, []).append(r.score)
        return out

    def mean_scores(self) -> dict[str, float]:
        return {k: statistics.mean(v) for k, v in self.scores_by_label().items()}

    def ranking_accuracy(self) -> float:
        """Доля правильно упорядоченных пар работ, усреднённая по прогонам.

        Сравнивается порядок, а не абсолютные значения: эталонных баллов
        преподавателя у нас нет, а относительный порядок задан самим
        репозиторием примеров.
        """
        per_run = [acc for acc, _ in self._per_run_pairs()]
        return statistics.mean(per_run) if per_run else 0.0

    def pair_details(self) -> list[dict[str, object]]:
        """Разбор по парам работ.

        Нужен, чтобы агрегат не скрывал, ГДЕ система ошибается. Доля
        «67 %» сама по себе не говорит, что одна пара разделена уверенно, а
        другая не разделена вовсе — а для выводов важно именно это.
        """
        order = [label for label, _ in CASES]
        out: list[dict[str, object]] = []
        for a in range(len(order)):
            for b in range(a + 1, len(order)):
                la, lb = order[a], order[b]
                gaps: list[float] = []
                correct = 0
                runs = 0
                for i in range(self.runs_count):
                    scores = {r.label: r.score for r in self.runs if r.run_index == i}
                    if la not in scores or lb not in scores:
                        continue
                    runs += 1
                    gaps.append(round(scores[lb] - scores[la], 2))
                    if scores[la] < scores[lb]:
                        correct += 1
                if not runs:
                    continue
                out.append({
                    "pair": f"{la} < {lb}",
                    "correct_runs": correct,
                    "runs": runs,
                    "mean_gap": round(statistics.mean(gaps), 2),
                    # Разрыв меньше половины балла на десятибалльной шкале
                    # означает, что работы не различены, а не упорядочены.
                    "separated": abs(statistics.mean(gaps)) >= 0.5 and correct == runs,
                })
        return out

    @property
    def runs_count(self) -> int:
        return max((r.run_index for r in self.runs), default=-1) + 1

    def _per_run_pairs(self) -> list[tuple[float, int]]:
        order = [label for label, _ in CASES]
        per_run: list[tuple[float, int]] = []
        for i in range(self.runs_count):
            scores = {r.label: r.score for r in self.runs if r.run_index == i}
            if len(scores) < 2:
                continue
            correct = total = 0
            for a in range(len(order)):
                for b in range(a + 1, len(order)):
                    la, lb = order[a], order[b]
                    if la not in scores or lb not in scores:
                        continue
                    total += 1
                    # Строгое неравенство: одинаковые баллы не различают работы.
                    if scores[la] < scores[lb]:
                        correct += 1
            if total:
                per_run.append((correct / total, total))
        return per_run

    def stability(self) -> dict[str, float]:
        """Разброс баллов между прогонами. При seed и temperature=0 ожидается 0."""
        return {
            label: (max(v) - min(v)) if len(v) > 1 else 0.0
            for label, v in self.scores_by_label().items()
        }

    def evidence_rate(self) -> float:
        total = sum(r.evidence_total for r in self.runs)
        verified = sum(r.evidence_verified for r in self.runs)
        return verified / total if total else 0.0

    def mean_duration(self) -> float:
        return statistics.mean([r.duration_s for r in self.runs]) if self.runs else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "started_at": self.started_at,
            "runs": [r.__dict__ for r in self.runs],
            "summary": {
                "mean_scores": self.mean_scores(),
                "ranking_accuracy": self.ranking_accuracy(),
                "stability_spread": self.stability(),
                "evidence_verified_rate": self.evidence_rate(),
                "mean_duration_s": self.mean_duration(),
                "runs_per_case": self.runs_count,
                "pairs": self.pair_details(),
            },
        }


def _to_case_run(label: str, i: int, result: ReviewResult) -> CaseRun:
    return CaseRun(
        label=label,
        run_index=i,
        score=result.preliminary_score,
        max_score=result.max_score,
        duration_s=round(result.duration_ms / 1000, 1),
        evidence_total=sum(c.evidence_total for c in result.criteria),
        evidence_verified=sum(c.evidence_verified for c in result.criteria),
        failed_steps=result.failed_steps,
        failed_criteria=sum(1 for c in result.criteria if c.failed),
        ai_score=float((result.ai_signal or {}).get("score", 0.0)),
        ai_confidence=str((result.ai_signal or {}).get("confidence", "")),
        formal_violations=sum(1 for f in result.formal if f.get("status") == "fail"),
        per_criterion={c.criterion_id: c.score for c in result.criteria},
    )


def load_rubric(*, fast: bool = False, cache: Path | None = None) -> Rubric:
    """Рубрика для бенчмарка.

    Кэш инвалидируется при изменении промптов извлечения. Иначе он тихо
    расходится с тем, что производит приложение: наблюдалось прямо —
    закэшированная рубрика содержала 5 признаков у критерия «Качество
    оформления», а свежая 9, и баллы бенчмарка перестали совпадать с
    баллами приложения. Бенчмарк, измеряющий не то, что работает
    в продукте, хуже отсутствующего.

    Ключ кэша включает хеш промптов извлечения и файла условия.
    """
    import hashlib

    from app.rubric import extract as extract_mod

    cache_dir = (cache.parent if cache else PROJECT_ROOT / "data")
    fingerprint = hashlib.sha256(
        (
            extract_mod._SYSTEM
            + str(sorted(extract_mod.extract_deterministic(read_any(CONDITION).text)))
            + CONDITION.read_bytes().hex()[:64]
        ).encode()
    ).hexdigest()[:12]
    cache = cache or (cache_dir / f"rubric_cache_{fingerprint}.json")

    if cache.exists():
        rubric = Rubric.model_validate_json(cache.read_text(encoding="utf-8"))
    else:
        # Устаревшие кэши других отпечатков убираем, чтобы не копились.
        for old in cache_dir.glob("rubric_cache_*.json"):
            old.unlink(missing_ok=True)
        rubric = extract_rubric(read_any(CONDITION), track="product_fraud", fast=fast)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(rubric.model_dump_json(indent=2), encoding="utf-8")

    rubric.approve(by="bench")
    return rubric


def run_bench(
    *,
    runs: int = 3,
    fast: bool = False,
    out: Path | None = None,
    verbose: bool = True,
) -> BenchReport:
    """Прогоняет бенчмарк и сохраняет отчёт."""
    rubric = load_rubric(fast=fast)
    condition = read_any(CONDITION)
    report = BenchReport(started_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    for i in range(runs):
        for label, path in CASES:
            if not path.exists():
                if verbose:
                    print(f"  пропуск {label}: нет файла {path.name}")
                continue
            if verbose:
                print(f"  прогон {i + 1}/{runs}: {label:8} … ", end="", flush=True)
            result = run_review(
                read_any(path), rubric, condition=condition,
                submission_id=f"{label}-{i}", fast=fast,
            )
            report.model = result.model
            run = _to_case_run(label, i, result)
            report.runs.append(run)
            if verbose:
                print(
                    f"{run.score:g}/{run.max_score:g}  "
                    f"цитат {run.evidence_verified}/{run.evidence_total}  "
                    f"{run.duration_s:.0f}с"
                    + (f"  ОТКАЗЫ: {run.failed_steps}" if run.failed_steps else "")
                )

    out = out or (PROJECT_ROOT / "data" / "bench_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if verbose:
        print(f"\nОтчёт сохранён: {out}")
    return report


def print_summary(report: BenchReport) -> None:
    s = report.to_dict()["summary"]  # type: ignore[index]
    print("\n" + "=" * 66)
    print("РЕЗУЛЬТАТЫ БЕНЧМАРКА")
    print("=" * 66)
    print(f"модель: {report.model}, прогонов на работу: {s['runs_per_case']}")
    print("\nСредний балл:")
    for label, _ in CASES:
        if label in s["mean_scores"]:  # type: ignore[index]
            spread = s["stability_spread"][label]  # type: ignore[index]
            print(f"  {label:10} {s['mean_scores'][label]:5.2f}"  # type: ignore[index]
                  f"   разброс между прогонами: {spread:g}")
    print(f"\nправильность ранжирования: {s['ranking_accuracy']:.0%}")  # type: ignore[index]

    # Разбор по парам печатается всегда: агрегат не должен скрывать, ГДЕ
    # система ошибается. «67 %» само по себе не говорит, что две пары
    # разделены уверенно, а третья не разделена вовсе.
    print("\nпо парам работ:")
    for pair in s["pairs"]:  # type: ignore[index]
        verdict = "разделены" if pair["separated"] else "НЕ РАЗДЕЛЕНЫ"
        print(
            f"  {pair['pair']:20} верно {pair['correct_runs']}/{pair['runs']} прогонов, "
            f"разрыв {pair['mean_gap']:+.1f} — {verdict}"
        )

    print(f"\nподтверждённых цитат:      {s['evidence_verified_rate']:.0%}")  # type: ignore[index]
    print(f"среднее время на работу:   {s['mean_duration_s']:.0f} с")  # type: ignore[index]


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Бенчмарк качества ревью")
    p.add_argument("--runs", type=int, default=3, help="прогонов на каждую работу")
    p.add_argument("--fast", action="store_true", help="younger-модель")
    p.add_argument("--out", help="путь к отчёту JSON")
    a = p.parse_args()

    print_summary(
        run_bench(runs=a.runs, fast=a.fast, out=Path(a.out) if a.out else None)
    )
