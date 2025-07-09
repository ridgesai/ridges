from fastapi import APIRouter

router = APIRouter()

async def healthcheck():
    return "OK"

routes = [
    ("/healthcheck", healthcheck),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["healthcheck"],
        methods=["GET"]
    )