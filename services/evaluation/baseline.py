"""The regression baseline: the last-accepted Tier 1 scores, and the gate.

A checked-in ``data/eval/baseline.json`` records the metric scores a human has
accepted as the current bar. A run passes the gate when every baseline metric is
matched to within a documented ``tolerance``; it fails when any metric drops below
``baseline - tolerance``. That is the CI-blocking regression gate (ADR 0017).

Two deliberate asymmetries:

- **Only downward moves fail.** A run that scores *higher* than baseline passes —
  improving is never a regression. But it does **not** rewrite the file: raising
  the bar is a reviewed human decision (someone confirms the gain is real and
  intended), never a side effect of a green run, or the gate would ratchet itself
  and stop catching anything.
- **A missing metric fails, loudly.** If the baseline names ``mrr`` and a run
  reports no ``mrr``, that is a broken evaluator, not a pass — so it is a failure,
  not a skip (CLAUDE.md: fail loud).

The ``tolerance`` absorbs nothing but genuine float noise here — the Tier 1
pipeline is fully deterministic — but it is honoured so the mechanism is correct
the moment a future metric (or a Voyage-backed tier) introduces real variance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from shared.observability import traced

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent
DEFAULT_BASELINE_PATH: Final[Path] = _REPO_ROOT / "data" / "eval" / "baseline.json"


@dataclass(frozen=True, slots=True)
class Baseline:
    """The accepted scores and how far below them a run may fall."""

    metrics: Mapping[str, float]
    k: int
    tolerance: float


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """One metric's baseline, observed value, and whether it held."""

    name: str
    baseline: float
    observed: float
    floor: float
    passed: bool


@dataclass(frozen=True, slots=True)
class RegressionResult:
    """The outcome of comparing a run's scores against the baseline."""

    passed: bool
    comparisons: tuple[MetricComparison, ...]

    def summary(self) -> str:
        """A one-line-per-metric human summary, for the operator script and CI log."""
        lines = []
        for c in self.comparisons:
            mark = "ok" if c.passed else "REGRESSED"
            lines.append(
                f"  [{mark}] {c.name}: observed {c.observed:.4f} "
                f"vs baseline {c.baseline:.4f} (floor {c.floor:.4f})"
            )
        return "\n".join(lines)


@traced
def load_baseline(path: Path | None = None) -> Baseline:
    """Read ``data/eval/baseline.json`` (or ``path``).

    Raises:
        FileNotFoundError: if the baseline file is absent — without it there is no
            bar to hold a run to, which is a setup error, not a pass.
        ValueError: if the file is malformed (missing/typed-wrong fields).
    """
    baseline_path = path or DEFAULT_BASELINE_PATH
    if not baseline_path.is_file():
        raise FileNotFoundError(f"eval baseline does not exist: {baseline_path}")

    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"baseline must be a JSON object, got {type(raw).__name__}")

    metrics = raw.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("baseline needs a non-empty 'metrics' object")
    parsed_metrics: dict[str, float] = {}
    for name, value in metrics.items():
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"baseline metric '{name}' must be a number, got {value!r}")
        parsed_metrics[name] = float(value)

    tolerance = raw.get("tolerance")
    if not isinstance(tolerance, int | float) or isinstance(tolerance, bool) or tolerance < 0:
        raise ValueError(f"baseline 'tolerance' must be a non-negative number, got {tolerance!r}")

    k = raw.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError(f"baseline 'k' must be a positive integer, got {k!r}")

    return Baseline(metrics=parsed_metrics, k=k, tolerance=float(tolerance))


@traced
def check_regression(observed: Mapping[str, float], baseline: Baseline) -> RegressionResult:
    """Compare a run's ``observed`` scores against ``baseline``.

    Every metric named in the baseline must be present in ``observed`` and at or
    above ``baseline - tolerance``. A metric the run did not report counts as
    ``observed == 0.0`` (fail loud on a broken evaluator, not a silent skip).
    """
    comparisons: list[MetricComparison] = []
    for name, baseline_value in baseline.metrics.items():
        observed_value = observed.get(name, 0.0)
        floor = baseline_value - baseline.tolerance
        comparisons.append(
            MetricComparison(
                name=name,
                baseline=baseline_value,
                observed=observed_value,
                floor=floor,
                passed=observed_value >= floor,
            )
        )
    return RegressionResult(
        passed=all(c.passed for c in comparisons),
        comparisons=tuple(comparisons),
    )
