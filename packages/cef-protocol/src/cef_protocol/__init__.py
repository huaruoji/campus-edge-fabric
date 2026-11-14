"""Campus Edge Fabric shared protocol layer.

Everything that both the coordinator and the worker agents must agree on lives here:
wire models, task/job states, workload parameter schemas and a thin HTTP client SDK.
"""

from .client import CefClient
from .ids import new_id
from .states import JobState, TaskState, WorkerState
from .workloads import WORKLOADS, WorkloadKind, param_model, validate_params

__all__ = [
    "CefClient",
    "JobState",
    "TaskState",
    "WorkerState",
    "WORKLOADS",
    "WorkloadKind",
    "new_id",
    "param_model",
    "validate_params",
]
