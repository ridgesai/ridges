from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from queries.errors import CompetitionAdminConflictError, CompetitionNotFoundError
from utils.bittensor import SubtensorUnavailableError


async def _competition_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def _competition_admin_conflict_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _subtensor_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"detail": "Subtensor is currently unavailable. Please retry shortly."}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(CompetitionNotFoundError, _competition_not_found_handler)
    app.add_exception_handler(CompetitionAdminConflictError, _competition_admin_conflict_handler)
    app.add_exception_handler(SubtensorUnavailableError, _subtensor_unavailable_handler)
