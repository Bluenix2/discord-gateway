import zlib
from collections import deque
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urlencode

from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import (
    BytesMessage, CloseConnection, Event, Ping, RejectConnection, Request,
    TextMessage
)

from .errors import CloseDiscordConnection, ConnectionRejected
from .opcode import Opcode

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

    Attributes:
        uri: The URI that was configured to connect to.
        encoding: Either 'json' or 'etf' for the encoding used.
        compress:
            If a boolean, indicates whether to use payload compression. On the
            other hand, if a string indicates the transport compression to use
            (can only be 'zlib-stream' at the moment).
        session_id: The session ID from Discord.
        sequence: Current sequence of events, used when resuming.
        acknowledged: Whether the last heartbeat was acknowledged.
        heartbeat_interval: Amount of seconds to sleep between heartbeats.
    """

    uri: str
    encoding: str
    compress: Union[str, bool]

    session_id: Optional[str]
    sequence: Optional[int]

    acknowledged: bool
    heartbeat_interval: Optional[float]

    __slots__ = (
        'uri', 'encoding', 'compress', 'dispatch_handled', 'session_id',
        'sequence', '_events', 'should_resume', '_proto', 'acknowledged',
        'heartbeat_interval', '_events', '_bytes_buffer', '_text_buffer',
        '_inflator',
    )

    def __init__(
        self,
        uri: str,
        *,
        encoding: str,
        compress: Union[str, bool] = False,
        dispatch_handled: bool = False,
    ) -> None:
        """Initialize a Discord Connection.

        The parameters passed here will be used when initializing the WebSocket
        connection to Discord.

        Parameters:
            uri:
                URI to open a websocket to. This should be requested from the
                Get Gateway or Get Gateway Bot endpoints.
            encoding:
                Encoding to use, either JSON or binary ETF. If using ETF the
                client cannot send compressed messages to the server.
                Snowflakes are also transmitted as 64-bit integers as opposed
                to strings. Either 'json' for JSON, or 'etf' for binary ETF.
            compress:
                Transport compression to use, this is different from payload
                compression and both cannot be used at the same time. Payload
                compression is specified when IDENTIFYing. Specify
                'zlib-stream' for transport compression.
            dispatch_handled:
                Whether to dispatch automatically handled events. Examples of
                these types of events are HEARTBEAT_ACK and RECONNECT. When
                this is set to False these events are not dispatched.
        """
        if encoding == 'etf' and not ERLPACK_AVAILABLE:
            raise ValueError("ETF encoding not available without 'erlpack' installed")

        if uri.endswith('/'):
            uri = uri[:-1]

        if uri.startswith('wss://'):
            uri = uri[6:]

        self.uri = uri
        self.encoding = encoding
        self.compress = compress

        self.dispatch_handled = dispatch_handled

        self.session_id = None
        self.sequence = None

        self._events = deque()  # Buffer of events received

        # This will initialize the rest of the attributes
        self.reconnect()

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

        The tuple has two items representing the host and port to open a TCP
        socket to.
        """
        # The gateway uses secure WebSockets (wss) hence port 443
        return self.uri, 443

    @property
    def closing(self) -> bool:
        """Whether the connection is closing.

        When this is true no heartbeat should be sent as a closing handshake
        is in progress. The best course of action is to simply skip sending
        the heartbeat and sleep another heartbeat interval.
        """
        return self._proto.state in {
            ConnectionState.CLOSED, ConnectionState.LOCAL_CLOSING,
            ConnectionState.REMOTE_CLOSING
        }

    def _encode(self, payload: Any) -> Event:
        """Prepare a payload to be sent to the gateway.

        This method will encode the payload in the configured encoding - either
        a JSON TextMessage Frame or ETF BytesMessage frame.
        """
        if self.encoding == 'json':
            return TextMessage(json_dumps(payload))
        else:
            # The encoding is ETF because these are only two cases
            return BytesMessage(etf_pack(payload))

    def reconnect(self) -> None:
        """Reinitialize the connection.

        This should be called when the TCP socket is re-established and will
        reset the internal state of the WebSocket to handle a new connection.

        The only thing not reset is the `should_resume` attribute which is
        set when disconnecting.
        """
        # This method is called when the user is reconnecting using another TCP
        # socket, we don't want to override the bool that may have been set
        # when closing.
        if getattr(self, 'should_resume', None) is None:
            self.should_resume = None

        self._proto = WSConnection(ConnectionType.CLIENT)

        self.acknowledged = True
        self.heartbeat_interval = None

        self._bytes_buffer = bytearray()
        self._text_buffer = ''
        self._inflator = zlib.decompressobj()

    def events(self) -> Generator[Dict[str, Any], None, None]:
        """Generator that yields events which have been received.

        In general it is recommended to call this in a for-loop anytime data
        has been received as to handle whatever was received.

        This will consume an internal deque until no more items can be removed
        and return, meaning that events are removed when retrieved so that no
        duplicates appear.
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

    def close(self, code: int = 1001) -> bytes:
        """Generate the bytes to send a closing frame to the WebSocket.

        After having sent this you should continue receiving bytes and calling
        `receive()` until `CloseDiscordConnection` is raised at which point the
        TCP socket should be closed.

        Parameters:
            code:
                The reasoning of closing the WebSocket as close code. Both 1000
                and 1001 (default) close the session which means when
                reconnecting a new session has to be created using an IDENTIFY.
        """
        return self._proto.send(CloseConnection(code))

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
                self.should_resume = True
                return self._proto.send(CloseConnection(1008))

            self.acknowledged = False

        return self._proto.send(self._encode({
            'op': 1,
            'd': self.sequence,
        }))

    def _handle_event(self, event: Dict[str, Any]) -> Tuple[bool, Optional[bytes]]:
        """Handle a Discord event and potentially send a response.

        Because there are several ways that data can be received this has been
        separated into another internal method. It returns a tuple, the first
        item is a bool whether the event should be returned to the user and the
        second item is a potential response in bytes.
        """
        if event.get('s') is not None:
            self.sequence = event['s']

        if event['op'] == Opcode.HEARTBEAT:
            # Discord has sent a HEARTBEAT and expects an immediate response
            return False, self.heartbeat(acknowledge=False)

        elif event['op'] == Opcode.HEARTBEAT_ACK:
            # Acknowlegment of our heartbeat
            self.acknowledged = True
            return False, None

        elif event['op'] == Opcode.HELLO:
            # Discord sends the interval in milliseconds
            self.heartbeat_interval = event['d']['heartbeat_interval'] / 1000
            return True, None

        elif event['op'] == Opcode.DISPATCH and event['t'] == 'READY':
            self.session_id = event['d']['session_id']
            return True, None

        elif event['op'] == Opcode.RECONNECT:
            # Discord wants us to reconnect and resume, because of how the
            # WebSocket protocol works the server will respond with a
            # CloseConnection message and we raise the CloseDiscordConnection
            # exception there.
            self.should_resume = True
            # There really isn't a completely fitting error code here
            return False, self._proto.send(CloseConnection(1008))

        elif event['op'] == Opcode.INVALID_SESSION:
            # This is documented to be sent if:
            # - The gateway could not initialize a session from an IDENTIFY
            # - The gateway could not resume a session
            # - The gateway has invalidated an active session
            # The 'd' key indicates whether we should resume
            self.should_resume = event['d']
            return False, self._proto.send(CloseConnection(1008))

        return True, None

    def receive(self, data: Optional[bytes]) -> List[bytes]:
        """Receive data from the WebSocket.

        This method may return new data to send back, in cases such as PING
        frames or HEARTBEAT events which require an immediate HEARTBEAT command
        be sent back to it.

        Parameters:
            data: The bytes received from the TCP socket.

        Raises:
            RuntimeError: Compressed event received with no compression.

        Returns:
            A list of bytes to respond back with. See `events()` for how to
            get the events received.
        """
        # WSProto uses None instead of an empty byte string.
        if data is not None and len(data) == 0:
            data = None

        self._proto.receive_data(data)

        res = []

        for event in self._proto.events():
            if isinstance(event, Ping):
                res.append(self._proto.send(event.response()))
                continue

            elif isinstance(event, RejectConnection):
                raise ConnectionRejected(event)

            elif isinstance(event, CloseConnection):
                # This may or may not have been initiated by us, either way the
                # best option is to close the websocket and RESUME
                if self.should_resume is None:
                    # This wasn't initiated by us, the best bet is to RESUME
                    self.should_resume = True

                if self._proto.state == ConnectionState.CLOSED:
                    # We initiated the closing and have now received a reply,
                    # WSProto yields a CloseConnection to the initiatior (us)
                    raise CloseDiscordConnection(None)
                else:
                    # It should be ConnectionState.REMOTE_CLOSING and we need
                    # to reply to the closure
                    raise CloseDiscordConnection(self._proto.send(event.response()))

            elif isinstance(event, TextMessage):
                # Compressed message will only show up as ByteMessage events,
                # we can interpret this as a full JSON payload.
                self._text_buffer += event.data

                if not event.message_finished:
                    continue

                payload = json_loads(self._text_buffer)
                self._text_buffer = ''

            elif isinstance(event, BytesMessage):
                if self.compress == 'zlib-stream':
                    self._bytes_buffer.extend(event.data)

                    if not event.message_finished:
                        continue

                    if len(self._bytes_buffer) < 4 or self._bytes_buffer[-4:] != ZLIB_SUFFIX:
                        # The message is finished but our data doesn't end with
                        # the correct ZLIB suffix... there isn't really any
                        # sensible way to recover from this.
                        raise RuntimeError('Finished compressed message without ZLIB suffix')

                    # The Zlib suffix has been sent and our buffer should be
                    # full with a complete message
                    if self.encoding == 'json':
                        payload = json_loads(self._inflator.decompress(self._bytes_buffer))
                    else:
                        payload = etf_unpack(self._inflator.decompress(self._bytes_buffer))

                    self._bytes_buffer = bytearray()  # Reset our buffer

                elif self.compress is True:
                    self._bytes_buffer.extend(event.data)

                    if not event.message_finished:
                        continue

                    payload = json_loads(zlib.decompress(self._bytes_buffer))
                    self._bytes_buffer = bytearray()

                elif self.encoding == 'etf':
                    self._bytes_buffer.extend(event.data)

                    if not event.message_finished:
                        continue

                    payload = etf_unpack(self._bytes_buffer)
                    self._bytes_buffer = bytearray()

                else:
                    raise RuntimeError('Received bytes message when no compression specified')
            else:
                # The code below is shared for TextMessage and BytesMessage but
                # any other event we have received shouldn't cause this to run.
                continue

            dispatch, response = self._handle_event(payload)

            if self.dispatch_handled or dispatch:
                self._events.append(payload)

            if response is not None:
                res.append(response)

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

        Consider using `resume()` if possible, there is a ratelimit on how
        often you can IDENTIFY.

        Parameters:
            token: The Discord authorization token to IDENTIFY with.
            itents: Intents indicating what events will be received.
            properties: Properties about the connection.
            compress: Whether to use payload compression.
            large_threshold: How big a guild has to be to be considered large.
            shard: A two-integer tuple specifying the shard ID.
            presence: Initial presence information to start with.

        Returns:
            The bytes returned should be directly sent to the TCP socket.
        """
        self.should_resume = None  # Reset the state for the next reconnection

        data = {
            'token': token,
            'intents': intents,
            'properties': properties
        }

        if compress is not None:
            data['compress'] = compress
            self.compress = compress

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

    def resume(self, token: str) -> bytes:
        """Generate a RESUME command from the current state.

        Parameters:
            token: The Discord authorization token to RESUME with.

        Returns:
            The bytes to send to the TCP socket.
        """
        self.should_resume = None

        return self._proto.send(self._encode({
            'op': Opcode.RESUME,
            'd': {
                'token': token,
                'session_id': self.session_id,
                'seq': self.sequence
            },
        }))
