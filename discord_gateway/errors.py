from typing import Optional

from wsproto.events import RejectConnection

__all__ = ('CloseDiscordConnection', 'ConnectionRejected')


class CloseDiscordConnection(Exception):
    """Signalling exception notifying the socket should be closed.

    The `data` attribute contains any potentially last bytes to send before
    closing the TCP socket - or None indicating that nothing should be sent.

    The `code` attribute is the close code and the `reason` attribute optionally
    contains the reason of the closure.
    """

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

    def __init__(self, event: RejectConnection) -> None:
        super().__init__(
            f'Discord rejected the WebSocket connection - Error code {event.status_code}'
        )

        self.code = event.status_code
        self.headers = event.headers
