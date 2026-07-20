"""Loading the checked-in evaluation dataset.

The dataset is a small JSON file of graded queries under ``data/eval/`` — the
same spirit as ``data/corpus/`` is to retrieval: fixed, committed, and runnable
with no live dependency. Each entry names a query, the ``document_id`` values that
should answer it, and a short note recording *why* those documents are the
expected answer, so the dataset is auditable rather than arbitrary (ADR 0017).

Parsing is strict and fails loud (CLAUDE.md): a malformed case is a broken gate,
not something to skip past silently. Every field is validated and every relevant
document id is required to be non-empty, because an empty relevant set makes
recall@k a silent 0/0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from shared.observability import traced

from services.evaluation.base import EvalCase

# The dataset ships beside the corpus it grades, one directory up from the corpus
# files themselves so `load_corpus` never ingests it.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATASET_PATH: Final[Path] = _REPO_ROOT / "data" / "eval" / "dataset.json"


@traced
def load_eval_cases(path: Path | None = None) -> list[EvalCase]:
    """Read and validate the eval dataset. ``None`` means the shipped default.

    Raises:
        FileNotFoundError: if the dataset file does not exist — a wrong path is a
            configuration error worth failing on, not an empty run to mistake for
            a passing one.
        ValueError: if the JSON is not a list of well-formed cases. The message
            names the offending index so a bad edit is quick to find.
    """
    dataset_path = path or DEFAULT_DATASET_PATH
    if not dataset_path.is_file():
        raise FileNotFoundError(f"eval dataset does not exist: {dataset_path}")

    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"eval dataset must be a JSON list of cases, got {type(raw).__name__}")

    cases: list[EvalCase] = []
    for index, entry in enumerate(raw):
        cases.append(parse_case(entry, index))
    if not cases:
        raise ValueError(f"eval dataset at {dataset_path} is empty; there is nothing to evaluate")
    return cases


def parse_case(entry: object, index: int) -> EvalCase:
    """Turn one mapping into a validated :class:`EvalCase`.

    Shared by :func:`load_eval_cases` (parsing the JSON file) and the generic
    :meth:`Evaluator.evaluate` contract path (parsing caller-supplied mappings),
    so both reach an ``EvalCase`` through exactly the same validation. ``index``
    is only used to name the offending case in an error message.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"case {index} must be an object, got {type(entry).__name__}")

    query = entry.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"case {index} has a missing or empty 'query'")

    relevant = entry.get("relevant_document_ids")
    if not isinstance(relevant, list) or not relevant:
        raise ValueError(f"case {index} ('{query}') needs a non-empty 'relevant_document_ids' list")
    for document_id in relevant:
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError(f"case {index} ('{query}') has a non-string or empty document id")

    note = entry.get("note", "")
    if not isinstance(note, str):
        raise ValueError(f"case {index} ('{query}') has a non-string 'note'")

    return EvalCase(
        query=query,
        relevant_document_ids=frozenset(relevant),
        note=note,
    )
