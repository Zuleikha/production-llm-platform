"""Policy tests for `config/pip-audit-ignore.txt` (ADR 0019, addendum 1).

The pip-audit gate may skip a finding only when it is explicitly justified and
time-boxed. These tests keep the ignore list from becoming a silent, permanent
suppression: every entry needs an advisory id, a review-by date that has not
passed, and a written reason.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

_IGNORE_FILE = Path(__file__).resolve().parents[2] / "config" / "pip-audit-ignore.txt"
_ENTRY = re.compile(
    r"^(?P<id>(?:GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}|PYSEC-\d{4}-\d+|CVE-\d{4}-\d+))"
    r"\s+(?P<review_by>\d{4}-\d{2}-\d{2})"
    r"\s+(?P<reason>\S.*)$"
)


def _entries() -> list[str]:
    lines = _IGNORE_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_every_entry_has_an_id_a_review_date_and_a_reason() -> None:
    for entry in _entries():
        assert _ENTRY.match(entry), f"malformed pip-audit ignore entry: {entry!r}"


def test_no_entry_is_past_its_review_date() -> None:
    today = dt.datetime.now(tz=dt.UTC).date()
    for entry in _entries():
        match = _ENTRY.match(entry)
        assert match is not None
        review_by = dt.date.fromisoformat(match["review_by"])
        assert review_by >= today, (
            f"{match['id']} passed its review date ({review_by}): re-check for a fix "
            "and either upgrade, or re-justify with a new date"
        )


def test_entries_are_unique() -> None:
    ids = [entry.split()[0] for entry in _entries()]
    assert len(ids) == len(set(ids))
