"""State machines. Anything not listed here is an illegal transition."""

from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # finished, but some tasks permanently failed
    FAILED = "failed"
    CANCELED = "canceled"


class WorkerState(StrEnum):
    ONLINE = "online"
    DRAINING = "draining"
    OFFLINE = "offline"


TERMINAL_TASK_STATES = (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELED)
TERMINAL_JOB_STATES = (
    JobState.SUCCEEDED,
    JobState.PARTIAL,
    JobState.FAILED,
    JobState.CANCELED,
)


def classify_job(succeeded: int, failed: int, total: int) -> JobState:
    """Single source of truth for derived job state (used by API and tests)."""
    if total == 0:
        return JobState.FAILED
    if succeeded == total:
        return JobState.SUCCEEDED
    if succeeded + failed >= total:
        return JobState.PARTIAL if succeeded else JobState.FAILED
    return JobState.RUNNING
