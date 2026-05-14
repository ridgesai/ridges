from fastapi import APIRouter

from api.endpoints.validator import SESSION_ID_TO_VALIDATOR
from queries.agent import get_pending_work_counts

router = APIRouter()


@router.get("/pending-work")
async def get_pending_work():
    """Return queue depths for KEDA autoscaling and screener capacity for observability.

    KEDA's metrics-api trigger reads ``screener_1_pending`` and
    ``screener_2_pending`` via ``valueLocation``.  The ``*_connected``,
    ``*_busy``, and ``*_idle`` fields are not consumed by KEDA but are useful
    for dashboards.

    No authentication — this endpoint is ClusterIP-only.
    """
    counts = await get_pending_work_counts()

    for class_num in ("1", "2"):
        prefix = f"screener-{class_num}-"
        screeners = [v for v in SESSION_ID_TO_VALIDATOR.values() if v.hotkey.startswith(prefix)]
        busy = sum(1 for s in screeners if s.current_evaluation_id is not None)
        counts[f"screener_{class_num}_connected"] = len(screeners)
        counts[f"screener_{class_num}_busy"] = busy
        counts[f"screener_{class_num}_idle"] = len(screeners) - busy

    return counts
