from api.src.backend.entities import Client
from api.src.backend.queries.evaluation_runs import update_evaluation_run_logs


async def handle_evaluation_run_logs(client: Client, response_json: dict):
    """Handle evaluation run logs message from a validator or screener"""
    run_id = response_json["run_id"]
    logs = response_json["logs"]

    await update_evaluation_run_logs(run_id, logs)
