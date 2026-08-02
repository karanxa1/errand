"""Small orchestration guards: typed approval result, a step-count budget, and
a cancellation-aware sleep. Kept dependency-free so the engine
(`run_errand.py`) and the API layer (`main.py`) can share the exact same types.

These implement the loop/hang-safety and typed-approval recommendations from
`docs/analysis-agent-tooling.md` (#2 approval gate, #5 step cap + cancel token).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class ApprovalDecision:
    """Outcome of the human-in-the-loop gate.

    `approved` — operator confirmed the spend (passkey in UI).
    `reason`   — optional free-text on a typed decline.
    `approval_id` — stable id distinct from run_id; echoed in the audit trail so
                    the UI can correlate the request/response pair.
    `timed_out` — the gate expired before any decision arrived.
    """

    approved: bool
    approval_id: str
    reason: str | None = None
    timed_out: bool = False


class RunCancelled(Exception):
    """Raised cooperatively when the cancel token is set between steps."""


class StepBudgetExceeded(Exception):
    """Raised when a run exceeds its configured step cap (loop safety)."""


class StepBudget:
    """A hard cap on how many orchestrator steps a single run may take. A linear
    run needs only a handful; the cap exists so a misbehaving/looping caller
    cannot run unbounded work behind a single SSE stream."""

    def __init__(self, max_steps: int) -> None:
        self._max = max_steps
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def tick(self, step: str) -> None:
        self._used += 1
        if self._used > self._max:
            raise StepBudgetExceeded(
                f"Step budget exceeded ({self._used} > {self._max}); aborted at '{step}'."
            )


def check_cancel(cancel: asyncio.Event | None, step: str) -> None:
    """Cooperative cancellation point. Raises RunCancelled if the token is set."""
    if cancel is not None and cancel.is_set():
        raise RunCancelled(f"Run cancelled before '{step}'.")


async def cancellable_sleep(seconds: float, cancel: asyncio.Event | None) -> bool:
    """Sleep up to `seconds`, but wake early (returning True) if `cancel` fires.

    Returns True if cancelled during the wait, False if the full sleep elapsed.
    Used by the credential poll loop so an aborted run doesn't keep polling."""
    if cancel is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(cancel.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False
