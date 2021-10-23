import zlib
from collections import deque
from typing import Any, Dict, Generator, List, Literal, Optional, Tuple, overload
from urllib.parse import urlencode

from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    BytesMessage, CloseConnection, Event, Ping, RejectConnection, Request,
    TextMessage
)

from ._errors import CloseDiscordConnection, ConnectionRejected
from ._opcode import Opcode

try:
    from erlpack import pack as etf_pack
    from erlpack import unpack as etf_unpack
    ERLPACK_AVAILABLE = True
except ImportError:
    # There is no fallback, we raise an exception later on.
    ERLPACK_AVAILABLE = False

try:
    from ujson import dumps as json_dumps
    from ujson import loads as json_loads
except ImportError:
    from json import dumps as json_dumps
    from json import loads as json_loads


__all__ = ('DiscordConnection',)


ZLIB_SUFFIX = b'\x00\x00\xff\xff'


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
        if encoding == 'etf' and not ERLPACK_AVAILABLE:
            raise ValueError("ETF encoding not available without 'erlpack' installed")

        self._proto = WSConnection(ConnectionType.CLIENT)

        if uri.endswith('/'):
            uri = uri[:-1]

        if uri.startswith('wss://'):
            uri = uri[6:]

        self.uri = uri
        self.encoding = encoding
        self.compress = compress

        self.should_resume = False

        # State and memory having to do with the WebSocket
        self.sequence = None
        self._events = deque()  # Buffer of events received
        self.buffer = bytearray()
        self.inflator = zlib.decompressobj()
        self.acknowledged = True

    @property
    def query_params(self) -> str:
        """Query parameters to add to the URL depending on values chosen."""
        quote = {'v': 9, 'encoding': self.encoding}
        if self.compress == 'zlib-stream':
            quote['compress'] = self.compress
        return urlencode(quote)

    @property
    def destination(self) -> Tuple[str, int]:
        """Generate a destination to connect to in the form of a tuple.

        The tuple has two items, the host and the port to use.
        """
        # The gateway uses secure WebSockets (wss) hence port 443
        return self.uri, 443

    def _encode(self, payload: Any) -> Event:
        """Prepare a payload to be sent to the gateway.

        This method will encode the payload in the configured encoding.
        """
        if self.encoding == 'json':
            return TextMessage(json_dumps(payload))
        else:
            # The encoding is ETF because these are only two cases
            return BytesMessage(etf_pack(payload))

    def reconnect(self) -> None:
        """Reinitialize the connection.

        This is called when the WebSocket is reconnected to reset the internal
        state of this connection object.
        """
        self._proto = WSConnection(ConnectionType.CLIENT)

        self.inflator = zlib.decompressobj()
        self._events = deque()
        self.acknowledged = True

    def events(self) -> Generator[Dict[str, Any], None, None]:
        """Generator that yields events which have been received.

        This will consume an internal deque until no more items can be removed
        and return. Compared to simply iterating the deque this means that
        events will be removed as they're retrieved.
        """
        while True:
            try:
                yield self._events.popleft()
            except IndexError:
                # There are no more events to consume
                return

    def connect(self) -> bytes:
        """Generate the switching protocols bytes to convert to a WebSocket.

        The next step in the bootstrapping process is to continously receive
        and send data until an HELLO event and the first HEARTBEAT command
        has been sent.
        """
        return self._proto.send(Request(self.uri, '/?' + self.query_params))

    def heartbeat(self, *, acknowledge: bool = True) -> bytes:
        """Generate a HEARTBEAT command to send.

        If no HEARTBEAT_ACK event has been received this will automatically
        start to close the connection which continues in `receive()`. As per
        the documentation when a HEARTBEAT command hasn't been acknowledged.

        Parameters:
            acknowledge:
                Whether this HEARTBEAT should be acknowledged. This parameter
                is useful for cases like the spontaneous HEARTBEAT events the
                gateway may send that don't require acknowledgement when
                responded to as commands.
        """
        if acknowledge:
            if not self.acknowledged:
                # Our last HEARTBEAT wasn't acknowledged and per the
                # documentation we should disconnect with a non-1000 close code
                # and attempt to reconnect with a RESUME. Here the 1008
                # POLICY VIOLATION error code is used.
                return self._proto.send(CloseConnection(1008))

            self.acknowledged = False

        return self._proto.send(self._encode({
            'op': 1,
            'd': self.sequence,
        }))

    def _handle_event(self, event: Dict[str, Any]) -> Optional[bytes]:
        """Handle a Discord event and potentially send a response.

        Because there are several ways that data can be received this has been
        separated into another internal method.
        """
        if event['op'] == 1:
            # Discord has sent a HEARTBEAT and expects an immediate response
            return self.heartbeat(acknowledge=False)
        elif event['op'] == 11:
            # Acknowlegment of our heartbeat
            self.acknowledged = True

    def receive(self, data: bytes) -> List[bytes]:
        """Receive data from the WebSocket.

        This method may return new data to send back, in cases such as PING
        frames or HEARTBEAT events which require an immediate HEARTBEAT command
        be sent back to it.
        """
        self._proto.receive_data(data)

        res = []

        for event in self._proto.events():
            if isinstance(event, Ping):
                res.append(self._proto.send(event.response()))

            elif isinstance(event, RejectConnection):
                raise ConnectionRejected(event)

            elif isinstance(event, CloseConnection):
                # This may or may not have been initiated by us, either way the
                # best option is to close the websocket and RESUME
                self.should_resume = True

                # Signal to the user that they should respond and then close
                # the TCP connection.
                raise CloseDiscordConnection(
                    self._proto.send(event.response())
                )

            elif isinstance(event, TextMessage):
                # Compressed message will only show up as ByteMessage events,
                # we can interpret this as a full JSON payload.
                payload = json_loads(event.data)
                response = self._handle_event(payload)

                if response:
                    res.append(response)
                else:
                    # If there was no response by the _handle_event() call that
                    # means that this is an event we should hand to the user.
                    self._events.append(payload)

            elif isinstance(event, BytesMessage):
                if self.compress == 'zlib-stream':
                    self.buffer.extend(event.data)

                    if len(event.data) < 4 or event.data[-4:] != ZLIB_SUFFIX:
                        # It isn't the end of the event and there will be more
                        # coming
                        continue

                    # The Zlib suffix has been sent and our buffer should be
                    # full with a complete message
                    if self.encoding == 'json':
                        payload = json_loads(self.inflator.decompress(event.data))
                    else:
                        payload = etf_unpack(self.inflator.decompress(event.data))

                    self.buffer = bytearray()  # Reset our buffer

                elif self.compress is True:
                    payload = json_loads(zlib.decompress(event.data))

                elif self.encoding == 'etf':
                    payload = etf_unpack(event.data)

                else:
                    raise RuntimeError('Received bytes message when no compression specified')

                response = self._handle_event(payload)

                if response:
                    res.append(response)
                else:
                    # If there was no response by the _handle_event() call that
                    # means that this is an event we should hand to the user.
                    self._events.append(payload)

        return res

    def identify(
        self,
        *,
        token: str,
        intents: int,
        properties: Dict[str, Any],
        compress: bool = False,
        large_treshold: int = 50,
        shard: Optional[Tuple[int, int]] = None,
        presence: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """Generate an initial IDENTIFY payload.

        There is a ratelimit on how many times you can IDENTIFY, it is up to
        you to handle this.
        """
        self.token = token

        data = {
            'token': token,
            'intents': intents,
            'properties': properties
        }

        if compress:
            data['compress'] = compress

        if large_treshold is not None:
            data['large_treshold'] = large_treshold

        if shard is not None:
            data['shard'] = shard

        if presence is not None:
            data['presence'] = presence

        return self._proto.send(self._encode({
            'op': Opcode.IDENTIFY,
            'd': data
        }))

    @overload
    def resume(self) -> bytes:
        ...

    @overload
    def resume(
        self,
        *,
        token: str,
        session_id: str,
        sequence: int
    ) -> bytes:
        ...

    def resume(
        self,
        *,
        token: Optional[str] = None,
        session_id: Optional[str] = None,
        sequence: Optional[int] = None
    ) -> bytes:
        """Generate a RESUME command from the current state.

        It is possible to RESUME a connection unknown to this instance, for
        that all kwargs are required.
        """
        # We want to make sure we don't send a bad payload that Discord will
        # complain about

        if token is None and self.token is None:
            raise TypeError("'token' is a required argument when self.token is None")
        else:
            self.token = token

        if session_id is None and self.session_id is None:
            raise TypeError("'session_id' is a required argument when self.session_id is None")
        else:
            self.session_id = session_id

        if sequence is None and self.sequence is None:
            raise TypeError("'sequence' is a required argument when self.sequence is None")
        else:
            self.sequence = sequence

        return self._proto.send(self._encode({
            'op': Opcode.RESUME,
            'd': {
                'token': self.token,
                'session_id': self.session_id,
                'seq': self.sequence
            },
        }))
