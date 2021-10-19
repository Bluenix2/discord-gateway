"""Sans-I/O implementation of the Discord gateway.

Sans-I/O means that this implements no I/O (network) and operates purely on the
bytes given using `wsproto`.

It means that this implementation can be reused for libraries implemented in a
threading fashion or asyncio/trio/curio.
"""

from typing import Literal, Optional, Tuple, Dict, Any

from wsproto import WSConnection, ConnectionType

__all__ = ('DiscordConnection',)


class DiscordConnection:
    """Main class representing a connection to Discord.

    This wraps a `wsproto.WSConnection` object to provide an sans-I/O
    implementation that should be wrapped with a network layer.
    """

    def __init__(
        self,
        uri: str,
        *,
        encoding: Literal['json', 'etf'],
        compress: Optional[Literal['zlib-stream']] = None
    ) -> None:
        """Initialize a Discord Connection.

        The parameters passed here will be used when initializing the WebSocket
        connection to Discord.

        Parameters:
            uri:
                URI to open a websocket to. This should be requested from the
                Get Gateway or Get Gateway Bot endpoints.
            encoding:
                Encoding to use, either JSON or binrary ETF. If using ETF the
                client cannot send compressed messages to the server.
                Snowflakes are also transmitted as 64-bit integers as opposed
                to strings.
            compress:
                Transport compression to use, this is different from payload
                compression and both cannot be used at the same time. Payload
                compression is specified when IDENTIFYing.
        """
        self._proto = WSConnection(ConnectionType.CLIENT)

        self.uri = uri
        self.encoding = encoding
        self.compress = compress

    @property
    def destination(self) -> Tuple[str, int]:
        """Generate a destination to connect to in the form of a tuple.

        The tuple has two items, the host and the port to use.
        """
        return self.uri, 443  # The gateway uses secure WebSockets (wss)
data = socket.receive()

conn.receive(data)