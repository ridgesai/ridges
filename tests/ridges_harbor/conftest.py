import pytest


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"
