from typing import Union, Dict, Any

from aiohttp import ClientSession
from yarl import URL

from ..main.network import NetworkClient, HTTP_METHODS


class AioHttpClient(NetworkClient):
    async def ensure_network(self, url: Union[str, URL], *, method: str = "GET", timeout: float = 10.0, **kwargs: Any):
        pass

    async def request(self, method: "HTTP_METHODS", url: Union[str, URL], headers: Dict[str, str] = None,
                      data: Union[str, bytes] = None, **kwargs: Any):
        pass

    async def get(self, url: Union[str, URL], headers: Dict[str, str] = None, **kwargs: Any):
        pass

    async def post(self, url: Union[str, URL], data: Union[str, bytes], headers: Dict[str, str] = None, **kwargs: Any):
        pass

    async def put(self, url: Union[str, URL], data: Union[str, bytes], headers: Dict[str, str] = None, **kwargs: Any):
        pass

    async def delete(self, url: Union[str, URL], headers: Dict[str, str] = None, **kwargs: Any):
        pass

    async def patch(self, url: Union[str, URL], data: Union[str, bytes], headers: Dict[str, str] = None, **kwargs: Any):
        pass

    session: ClientSession

    def __init__(self):
        self.session = ClientSession()
