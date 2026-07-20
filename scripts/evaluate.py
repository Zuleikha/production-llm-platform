"""Evaluate the RAG retrieval pipeline and gate on regression.

    uv run python scripts/evaluate.py                 # Tier 1: score + gate (CI)
    uv run python scripts/evaluate.py --report-only   # Tier 1: score, never fail
    uv run python scripts/evaluate.py --update-baseline  # accept current scores
    RUN_LLM_JUDGE=1 uv run python scripts/evaluate.py --judge  # Tier 2 (COSTS $)

**Tier 1 (default) is hermetic and free.** It grades ``data/eval/dataset.json``
against the shipped corpus using the deterministic offline-hash embeddings the
``test`` profile uses (ADR 0011) - no Qdrant, no Voyage, no network - computes
recall@k and MRR, and **exits non-zero if a score drops below the baseline minus
its tolerance** (`data/eval/baseline.json`). That non-zero exit is the CI-blocking
regression gate (ADR 0017). This is the same shape as ``scripts/ingest.py``: an
operator/CI script, never a service boot hook.

**Tier 2 (`--judge`) makes real, billable Anthropic calls.** Same double opt-in
as the live contract test (ADR 0015): it refuses unless ``RUN_LLM_JUDGE=1`` is set
*and* a key is present *and* the profile is not ``test`` (which cannot construct a
real client at all, ADR 0009). Never run in CI, never without human confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# scripts/ is not a package; make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections.abc import Mapping, Sequence

from services.evaluation.base import CaseResult
from services.evaluation.baseline import (
    DEFAULT_BASELINE_PATH,
    Baseline,
    check_regression,
    load_baseline,
)
from services.evaluation.dataset import DEFAULT_DATASET_PATH, load_eval_cases
from services.evaluation.judge import LLMJudge, generate_grounded_answer
from services.evaluation.metrics import mean
from services.evaluation.retrieval import RetrievalEvaluator, build_offline_retriever
from shared.config import get_settings
from shared.logging import setup_logging

_DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "data" / "corpus"
# Excerpts to hand the judge in Tier 2. Small: the judge reads them all in its
# prompt, and a long list is cost, not signal.
_JUDGE_EXCERPTS = 3


async def _run_tier1(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(settings)

    cases = load_eval_cases(args.dataset)
    baseline = load_baseline(args.baseline)

    retriever = await build_offline_retriever(settings, args.corpus)
    evaluator = RetrievalEvaluator(retriever, k=baseline.k)
    report = await evaluator.run(cases)

    print(f"Tier 1 - retrieval evaluation ({len(report.cases)} cases, k={report.k})")
    for case in report.cases:
        mark = "  " if case.recall_at_k >= 1.0 else "! "
        print(
            f"{mark}recall@{report.k}={case.recall_at_k:.3f} rr={case.reciprocal_rank:.3f}  "
            f"want={sorted(case.relevant_document_ids)} "
            f"got={list(case.retrieved_document_ids[: report.k])}"
        )
    print(f"\nAggregate: {_format_metrics(report.metrics)}")

    if args.update_baseline:
        _write_baseline(args.baseline, report.metrics, baseline)
        print(f"\nBaseline updated at {args.baseline}. This is a deliberate, reviewed change.")
        return 0

    if args.report:
        _write_report(args.report, report.metrics, report.cases, report.k)
        print(f"Report written to {args.report}")

    regression = check_regression(report.metrics, baseline)
    print(f"\nRegression check (tolerance {baseline.tolerance}):")
    print(regression.summary())

    if regression.passed:
        print("\nPASS - no regression against baseline.")
        return 0
    if args.report_only:
        print("\nREGRESSED - but --report-only, so not failing.")
        return 0
    print("\nFAIL - scores dropped below baseline. Fix retrieval, or update the baseline")
    print("deliberately with --update-baseline if the change is a genuine improvement.")
    return 1


async def _run_judge(args: argparse.Namespace) -> int:
    """Tier 2 - LLM-as-judge. Real, billable Anthropic calls. Double opt-in."""
    if os.environ.get("RUN_LLM_JUDGE") != "1":
        print(
            "Tier 2 (LLM-as-judge) makes real, billable Anthropic API calls and is\n"
            "opt-in. Set RUN_LLM_JUDGE=1 to confirm you accept the cost, then re-run\n"
            "with --judge. (See ADR 0015/0017.)",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()
    setup_logging(settings)
    if settings.is_test:
        print(
            "The 'test' profile cannot construct a real Anthropic client (ADR 0009).\n"
            "Run the judge under dev/prod with a real ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set; the judge needs it to call the model.",
            file=sys.stderr,
        )
        return 2

    # Imported here, not at module scope: this is the only path that builds a real
    # model client, and importing it up top would run under Tier 1 too.
    from services.orchestrator.llm import AnthropicClient, build_llm_client

    cases = load_eval_cases(args.dataset)
    retriever = await build_offline_retriever(settings, args.corpus)
    client = build_llm_client(settings)
    judge = LLMJudge(client)

    faithfulness: list[float] = []
    citation: list[float] = []
    try:
        print(f"Tier 2 - LLM-as-judge over {len(cases)} cases. This spends money.\n")
        for case in cases:
            documents = await retriever.retrieve(case.query, top_k=_JUDGE_EXCERPTS)
            excerpts = [(d.id, d.text) for d in documents]
            answer = await generate_grounded_answer(client, case.query, excerpts)
            verdict = await judge.score(case.query, answer, excerpts)
            faithfulness.append(verdict.faithfulness)
            citation.append(verdict.citation_accuracy)
            print(
                f"  faithfulness={verdict.faithfulness:.2f} "
                f"citation_accuracy={verdict.citation_accuracy:.2f}  {case.query}"
            )
    finally:
        if isinstance(client, AnthropicClient):
            await client.aclose()

    print(
        f"\nAggregate: faithfulness={mean(faithfulness):.3f} citation_accuracy={mean(citation):.3f}"
    )
    print("Tier 2 is advisory - it does not gate CI (ADR 0017).")
    return 0


def _format_metrics(metrics: Mapping[str, float]) -> str:
    return ", ".join(f"{name}={value:.4f}" for name, value in sorted(metrics.items()))


def _write_report(
    path: Path, metrics: Mapping[str, float], cases: Sequence[CaseResult], k: int
) -> None:
    payload = {
        "k": k,
        "metrics": dict(metrics),
        "cases": [
            {
                "query": c.query,
                "relevant_document_ids": sorted(c.relevant_document_ids),
                "retrieved_document_ids": list(c.retrieved_document_ids),
                "recall_at_k": c.recall_at_k,
                "reciprocal_rank": c.reciprocal_rank,
            }
            for c in cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_baseline(path: Path, metrics: Mapping[str, float], previous: Baseline) -> None:
    """Overwrite the baseline with freshly observed scores, preserving its notes.

    Only ever reached via ``--update-baseline`` - a passing run never writes here
    (ADR 0017). The ``k``, ``tolerance`` and ``notes`` carry over from the existing
    file so accepting new scores does not silently reshape the gate.
    """
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    existing["metrics"] = {name: round(value, 6) for name, value in sorted(metrics.items())}
    existing["k"] = previous.k
    existing["tolerance"] = previous.tolerance
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dataset", type=Path, default=DEFAULT_DATASET_PATH, help="eval dataset JSON"
    )
    parser.add_argument(
        "--baseline", type=Path, default=DEFAULT_BASELINE_PATH, help="regression baseline JSON"
    )
    parser.add_argument(
        "--corpus", type=Path, default=_DEFAULT_CORPUS, help="corpus directory to evaluate against"
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="write a JSON report to this path"
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="run Tier 1 and print scores but never exit non-zero (dev use)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="overwrite the baseline with the current scores (a deliberate, reviewed action)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="run Tier 2 LLM-as-judge instead of Tier 1 - COSTS MONEY, needs RUN_LLM_JUDGE=1",
    )
    args = parser.parse_args()

    if args.judge:
        return asyncio.run(_run_judge(args))
    return asyncio.run(_run_tier1(args))


if __name__ == "__main__":
    raise SystemExit(main())
