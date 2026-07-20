"""Evaluation harness: metrics, the evaluator, the dataset, and the regression gate.

Everything here is hermetic — the Tier 1 pipeline uses the deterministic offline
hashing embeddings (ADR 0011) and the LLM-as-judge parts are exercised against the
scripted double (ADR 0009). No test makes a network call or needs a key.

The load-bearing tests, in the sense of "if this passed by accident the gate is
useless":

- :class:`TestMetrics` hand-verifies recall@k and reciprocal rank against rankings
  small enough to check on paper (a Stage 6 requirement, ADR 0017).
- :meth:`TestRegressionGate.test_a_regressed_run_fails_against_the_baseline` proves
  the gate actually fails on a deliberately corrupted retrieval result — not just
  that it passes on the happy path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from services.evaluation import metrics
from services.evaluation.base import EvalCase
from services.evaluation.baseline import Baseline, check_regression, load_baseline
from services.evaluation.dataset import (
    DEFAULT_DATASET_PATH,
    load_eval_cases,
    parse_case,
)
from services.evaluation.judge import (
    JudgeVerdict,
    LLMJudge,
    generate_grounded_answer,
    parse_verdict,
)
from services.evaluation.retrieval import (
    InMemoryCosineStore,
    RetrievalEvaluator,
    build_offline_retriever,
)
from services.orchestrator.llm import AssistantTurn, ScriptedLLMClient
from services.retrieval.base import DocumentChunk, RetrievedDocument

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shared.config import Settings

_CORPUS = Path(__file__).resolve().parents[2] / "data" / "corpus"


def _doc(document_id: str, *, score: float = 1.0, position: int = 0) -> RetrievedDocument:
    """A retrieved chunk of ``document_id`` — only the id/score/position matter here."""
    return RetrievedDocument(
        id=f"{document_id}:{position}",
        text=f"text of {document_id}",
        score=score,
        document_id=document_id,
        source=document_id,
        position=position,
    )


class _FixedRetriever:
    """Returns a preset ranking for every query. Order is what the evaluator reads."""

    def __init__(self, documents: Sequence[RetrievedDocument]) -> None:
        self._documents = list(documents)

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> Sequence[RetrievedDocument]:
        return self._documents[:top_k] if top_k is not None else self._documents


class TestMetrics:
    """Hand-verifiable arithmetic — the core the whole gate rests on."""

    def test_recall_at_k_finds_the_only_relevant_doc_in_range(self) -> None:
        # ranked [A, B, C], relevant {B}, k=3 -> B is within top 3 -> 1/1 = 1.0
        assert metrics.recall_at_k(["A", "B", "C"], frozenset({"B"}), k=3) == 1.0

    def test_recall_at_k_misses_a_relevant_doc_below_k(self) -> None:
        # relevant {C} but k=2 only sees [A, B] -> 0 of 1 found -> 0.0
        assert metrics.recall_at_k(["A", "B", "C"], frozenset({"C"}), k=2) == 0.0

    def test_recall_at_k_is_a_fraction_of_multiple_relevant(self) -> None:
        # relevant {A, D}, top-3 [A, B, C] contains only A -> 1 of 2 -> 0.5
        assert metrics.recall_at_k(["A", "B", "C"], frozenset({"A", "D"}), k=3) == 0.5

    def test_recall_at_k_rejects_nonpositive_k(self) -> None:
        with pytest.raises(ValueError, match="k must be positive"):
            metrics.recall_at_k(["A"], frozenset({"A"}), k=0)

    def test_recall_at_k_rejects_an_empty_relevant_set(self) -> None:
        with pytest.raises(ValueError, match="no relevant documents"):
            metrics.recall_at_k(["A"], frozenset(), k=1)

    def test_reciprocal_rank_is_one_over_first_hit_position(self) -> None:
        # first relevant (B) is at position 2 -> 1/2
        assert metrics.reciprocal_rank(["A", "B", "C"], frozenset({"B"})) == 0.5

    def test_reciprocal_rank_is_one_when_top_is_relevant(self) -> None:
        assert metrics.reciprocal_rank(["A", "B"], frozenset({"A"})) == 1.0

    def test_reciprocal_rank_is_zero_when_nothing_relevant_retrieved(self) -> None:
        assert metrics.reciprocal_rank(["A", "B"], frozenset({"Z"})) == 0.0

    def test_mean_of_values(self) -> None:
        assert metrics.mean([1.0, 0.5, 0.0]) == 0.5

    def test_mean_of_empty_is_zero(self) -> None:
        assert metrics.mean([]) == 0.0


class TestInMemoryCosineStore:
    """The store must rank by real cosine similarity, not insertion order."""

    async def test_query_ranks_by_cosine_not_insertion_order(self) -> None:
        store = InMemoryCosineStore()
        # Insert the unrelated vector FIRST, so insertion order would rank it top.
        await store.upsert(
            [
                _chunk("unrelated", (0.0, 1.0, 0.0)),
                _chunk("aligned", (1.0, 0.0, 0.0)),
            ]
        )

        results = await store.query([1.0, 0.0, 0.0], top_k=2)

        assert [r.document_id for r in results] == ["aligned", "unrelated"]
        assert results[0].score == pytest.approx(1.0)
        assert results[1].score == pytest.approx(0.0)

    async def test_top_k_truncates(self) -> None:
        store = InMemoryCosineStore()
        await store.upsert([_chunk(f"d{i}", (float(i), 1.0, 0.0)) for i in range(5)])
        results = await store.query([1.0, 1.0, 0.0], top_k=2)
        assert len(results) == 2


class TestRetrievalEvaluator:
    """The evaluator turns a retriever's rankings into recall@k / MRR."""

    async def test_a_perfect_ranking_scores_one(self) -> None:
        evaluator = RetrievalEvaluator(_FixedRetriever([_doc("A"), _doc("B")]), k=3)
        report = await evaluator.run([EvalCase("q", frozenset({"A"}))])
        assert report.metrics == {"recall_at_k": 1.0, "mrr": 1.0}
        assert report.cases[0].retrieved_document_ids == ("A", "B")

    async def test_a_relevant_doc_ranked_second_halves_mrr_but_keeps_recall(self) -> None:
        evaluator = RetrievalEvaluator(_FixedRetriever([_doc("A"), _doc("B")]), k=3)
        report = await evaluator.run([EvalCase("q", frozenset({"B"}))])
        assert report.metrics["recall_at_k"] == 1.0
        assert report.metrics["mrr"] == 0.5

    async def test_chunks_of_one_document_collapse_to_that_document_once(self) -> None:
        # Two chunks of A ahead of B: A must appear once, at rank 1, not twice.
        retriever = _FixedRetriever([_doc("A", position=0), _doc("A", position=1), _doc("B")])
        evaluator = RetrievalEvaluator(retriever, k=3)
        report = await evaluator.run([EvalCase("q", frozenset({"B"}))])
        assert report.cases[0].retrieved_document_ids == ("A", "B")
        assert report.metrics["mrr"] == 0.5  # B is at rank 2, not rank 3

    async def test_the_generic_evaluate_contract_returns_the_same_metrics(self) -> None:
        """The ABC path parses plain mappings and returns the aggregate mapping."""
        evaluator = RetrievalEvaluator(_FixedRetriever([_doc("A")]), k=3)
        scores = await evaluator.evaluate([{"query": "q", "relevant_document_ids": ["A"]}])
        assert scores == {"recall_at_k": 1.0, "mrr": 1.0}

    def test_rejects_nonpositive_k(self) -> None:
        with pytest.raises(ValueError, match="k must be positive"):
            RetrievalEvaluator(_FixedRetriever([]), k=0)


class TestOfflinePipeline:
    """End-to-end Tier 1 over the real shipped corpus and dataset.

    Proves the dataset is answerable by the hermetic pipeline — i.e. it is not
    arbitrary — and that the numbers in the baseline are the ones this produces.
    """

    async def test_the_shipped_dataset_scores_at_or_above_baseline(
        self, settings: Settings
    ) -> None:
        retriever = await build_offline_retriever(settings, _CORPUS)
        evaluator = RetrievalEvaluator(retriever, k=3)
        report = await evaluator.run(load_eval_cases())

        baseline = load_baseline()
        result = check_regression(report.metrics, baseline)
        assert result.passed, result.summary()
        # Every shipped case retrieves its expected document at rank 1.
        assert report.metrics["recall_at_k"] == 1.0
        assert report.metrics["mrr"] == 1.0


class TestDataset:
    """Loading is strict: a malformed case is a broken gate, not a skipped one."""

    def test_the_shipped_dataset_loads(self) -> None:
        cases = load_eval_cases()
        assert len(cases) >= 4
        assert all(case.relevant_document_ids for case in cases)
        assert DEFAULT_DATASET_PATH.is_file()

    def test_a_missing_query_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing or empty 'query'"):
            parse_case({"relevant_document_ids": ["a.md"]}, 0)

    def test_an_empty_relevant_set_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'relevant_document_ids'"):
            parse_case({"query": "q", "relevant_document_ids": []}, 0)

    def test_a_non_object_case_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an object"):
            parse_case(["not", "an", "object"], 3)

    def test_a_missing_dataset_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="eval dataset does not exist"):
            load_eval_cases(tmp_path / "nope.json")


class TestRegressionGate:
    """The gate: passes on the baseline, fails on a regression, fails loud on gaps."""

    _BASELINE = Baseline(metrics={"recall_at_k": 1.0, "mrr": 1.0}, k=3, tolerance=0.05)

    def test_matching_scores_pass(self) -> None:
        result = check_regression({"recall_at_k": 1.0, "mrr": 1.0}, self._BASELINE)
        assert result.passed

    def test_a_drop_within_tolerance_passes(self) -> None:
        result = check_regression({"recall_at_k": 0.96, "mrr": 0.97}, self._BASELINE)
        assert result.passed

    def test_a_drop_below_tolerance_fails(self) -> None:
        result = check_regression({"recall_at_k": 0.90, "mrr": 1.0}, self._BASELINE)
        assert not result.passed
        assert any(not c.passed and c.name == "recall_at_k" for c in result.comparisons)

    def test_a_missing_metric_fails_loud_rather_than_skipping(self) -> None:
        # A run that reports no mrr is a broken evaluator, not a pass.
        result = check_regression({"recall_at_k": 1.0}, self._BASELINE)
        assert not result.passed

    async def test_a_regressed_run_fails_against_the_baseline(self, settings: Settings) -> None:
        """The required proof: a deliberately corrupted retrieval fails the gate.

        Every query is answered with the WRONG document (as a shrunk/broken
        retriever would), so recall and MRR collapse to 0 — well below the 0.95
        floor — and the gate reports a regression rather than passing.
        """
        wrong = RetrievalEvaluator(_FixedRetriever([_doc("nonexistent.md")]), k=3)
        report = await wrong.run(load_eval_cases())

        assert report.metrics["recall_at_k"] == 0.0
        assert report.metrics["mrr"] == 0.0
        result = check_regression(report.metrics, load_baseline())
        assert not result.passed, "a fully-wrong retrieval must fail the regression gate"


class TestBaselineLoading:
    """Baseline parsing is strict about its own shape."""

    def test_the_shipped_baseline_loads(self) -> None:
        baseline = load_baseline()
        assert baseline.k == 3
        assert "recall_at_k" in baseline.metrics
        assert baseline.tolerance >= 0

    def test_a_missing_baseline_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="eval baseline does not exist"):
            load_baseline(tmp_path / "nope.json")

    def test_a_baseline_without_metrics_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_text('{"metrics": {}, "k": 3, "tolerance": 0.05}', encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty 'metrics'"):
            load_baseline(path)


class TestJudge:
    """Tier 2 parsing and prompt-building — hermetic, via the scripted double."""

    def test_parse_verdict_reads_scores_and_reasoning(self) -> None:
        verdict = parse_verdict(
            'Here is my verdict: {"faithfulness": 0.9, "citation_accuracy": 1.0, '
            '"reasoning": "supported"} done.'
        )
        assert verdict == JudgeVerdict(
            faithfulness=0.9, citation_accuracy=1.0, reasoning="supported"
        )

    def test_parse_verdict_clamps_out_of_range_scores(self) -> None:
        verdict = parse_verdict('{"faithfulness": 1.4, "citation_accuracy": -0.2}')
        assert verdict.faithfulness == 1.0
        assert verdict.citation_accuracy == 0.0

    def test_parse_verdict_rejects_a_reply_with_no_json(self) -> None:
        with pytest.raises(ValueError, match="no JSON object"):
            parse_verdict("I think it was pretty good, honestly.")

    def test_parse_verdict_rejects_a_missing_score(self) -> None:
        with pytest.raises(ValueError, match="missing a numeric 'citation_accuracy'"):
            parse_verdict('{"faithfulness": 0.8}')

    def test_build_prompt_includes_query_answer_and_excerpts(self) -> None:
        prompt = LLMJudge.build_prompt("Q?", "A.", [("deployments.md:0", "roll back")])
        assert "Q?" in prompt
        assert "A." in prompt
        assert "[deployments.md:0] roll back" in prompt

    async def test_score_uses_the_client_and_parses_its_verdict(self) -> None:
        client = ScriptedLLMClient(
            [
                AssistantTurn(
                    text='{"faithfulness": 1.0, "citation_accuracy": 0.5, "reasoning": "ok"}',
                    stop_reason="end_turn",
                )
            ]
        )
        verdict = await LLMJudge(client).score("q", "a", [("d:0", "text")])
        assert verdict.faithfulness == 1.0
        assert verdict.citation_accuracy == 0.5

    async def test_generate_grounded_answer_streams_a_turn(self) -> None:
        client = ScriptedLLMClient(
            [AssistantTurn(text="Shift traffic back.", stop_reason="end_turn")]
        )
        answer = await generate_grounded_answer(client, "roll back?", [("d:0", "shift traffic")])
        assert answer == "Shift traffic back."


def _chunk(document_id: str, embedding: tuple[float, ...]) -> DocumentChunk:
    return DocumentChunk(
        id=f"{document_id}:0",
        text=f"text of {document_id}",
        embedding=embedding,
        document_id=document_id,
        source=document_id,
        position=0,
    )
