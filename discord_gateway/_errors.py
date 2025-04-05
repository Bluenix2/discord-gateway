from typing import List, Optional, Tuple

from wsproto.events import RejectConnection

__all__ = (
    'CloseDiscordConnection',
    'ConnectionRejected',
    'RejectedConnectionData',
)


class CloseDiscordConnection(Exception):
    """Signalling exception notifying the socket should be closed.

    The `data` attribute contains any potentially last bytes to send before
    closing the TCP socket - or None indicating that nothing should be sent.

    The `code` attribute is the close code and the `reason` attribute optionally
    contains the reason of the closure.
    """

    code: Optional[int]
    reason: Optional[str]

    data: Optional[bytes]

    def __init__(
        self,
        data: Optional[bytes],
        code: Optional[int] = None,
        reason: Optional[str] = None
    ) -> None:
        super().__init__(
            f"{code if code is not None else ''}{' - '+reason if reason is not None else ''}"
        )

        self.code = code
        self.reason = reason

        self.data = data


class ConnectionRejected(Exception):
    """Exception raised when the connection to Discord was rejected.

    This means that Discord rejected the WebSocket upgrade request. This is a
    fatal exception which cannot be recovered from (at least from
    discord-gateway's point of view) depending on the status code.
    """

    code: int
    headers: List[Tuple[bytes, bytes]]

    def __init__(self, event: RejectConnection) -> None:
        super().__init__(
            f'Discord rejected the WebSocket connection - Error code {event.status_code}'
        )

        self.code = event.status_code
        self.headers = event.headers


class RejectedConnectionData(Exception):
    """Exception raised to signal the body has been received."""

    data: bytes

    def __init__(self, data: bytes) -> None:
        super().__init__(
            'Complete HTTP response body for rejected WebSocket connection'
        )

        self.data = data
