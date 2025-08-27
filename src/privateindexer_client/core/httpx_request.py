import httpx

_client = None


def init_client(app_version: str):
    global _client
    _client = httpx.AsyncClient(
        headers={"User-Agent": f"privateindexer-client/{app_version}"}
    )


def get_client() -> httpx.AsyncClient:
    global _client
    return _client
