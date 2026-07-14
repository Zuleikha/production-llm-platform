"""Test doubles shared across test modules.

Kept out of ``conftest.py`` deliberately: pytest imports conftest as a top-level
module, so importing it again by path would create a second, distinct module
object. A plain module has no such hazard.
"""

from __future__ import annotations

from shared.datastores import Datastore


class FakeDatastore(Datastore):
    """A Datastore whose ping/connect behaviour is dictated by the test."""

    def __init__(self, name: str, *, configured: bool = True, fails: bool = False) -> None:
        self._name = name
        self._configured = configured
        self._fails = fails
        self.connect_calls = 0
        self.close_calls = 0
        self.ping_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def configured(self) -> bool:
        return self._configured

    async def connect(self) -> None:
        self.connect_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def ping(self) -> None:
        self.ping_calls += 1
        if self._fails:
            raise ConnectionError(f"{self._name} is down")
