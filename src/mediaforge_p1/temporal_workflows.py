from __future__ import annotations

from datetime import timedelta
from typing import Any

try:  # Optional dependency: the base API remains usable without Temporal.
    from temporalio import workflow
    from temporalio.common import RetryPolicy
except ImportError:  # pragma: no cover - exercised when the optional extra is absent
    workflow = None
    RetryPolicy = None


if workflow is not None:

    @workflow.defn
    class MediaForgeProductionWorkflow:
        @workflow.run
        async def run(self, request: dict[str, Any]) -> dict[str, Any]:
            timeout = int(request.get("activity_timeout_seconds", 3600))
            attempts = int(request.get("retry_max_attempts", 3))
            return await workflow.execute_activity(
                "mediaforge.execute_operation",
                request,
                start_to_close_timeout=timedelta(seconds=timeout),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=attempts,
                ),
            )

else:

    class MediaForgeProductionWorkflow:
        @staticmethod
        async def run(_request: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("install the optional Temporal dependency")
