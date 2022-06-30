from typing import Union
import pytest
from wsproto import ConnectionType, WSConnection
from wsproto.events import Request, AcceptConnection
from discord_gateway import DiscordConnection


@pytest.fixture(params=['json', 'etf'])
def encoding(request) -> str:
    return request.param


@pytest.fixture(params=['zlib-stream', True, False])
def compress(request) -> Union[str, bool]:
    return request.param


@pytest.fixture()
def connection(encoding: str, compress: Union[str, bool]) -> DiscordConnection:
    return DiscordConnection('wss://gateway.discord.gg/', encoding=encoding, compress=compress)


@pytest.fixture()
def server(connection: DiscordConnection, encoding: str, compress: Union[str, bool]) -> WSConnection:
    server = WSConnection(ConnectionType.SERVER)

    server.receive_data(connection.connect())

    for event in server.events():
        assert isinstance(event, Request)
        connection.receive(server.send(AcceptConnection()))
