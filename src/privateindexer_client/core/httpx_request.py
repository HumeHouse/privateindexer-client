from httpx import AsyncClient

from privateindexer_client.core.config import APP_VERSION


def get_client() -> AsyncClient:
    return AsyncClient(
        headers={"User-Agent": f"privateindexer-client/{APP_VERSION}"}
    )
