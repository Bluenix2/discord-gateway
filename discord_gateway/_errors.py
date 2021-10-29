from typing import Optional

from wsproto.events import RejectConnection

__all__ = ('CloseDiscordConnection', 'ConnectionRejected')


class CloseDiscordConnection(Exception):
    """Signalling exception notifying the socket should be closed.

    Contained in this exception is some last bytes to send off before closing
    the WebSocket.
    """

    def __init__(self, data: Optional[bytes]) -> None:
        super().__init__()

        self.data = data


class ConnectionRejected(Exception):
    """Exception raised when the connection to Discord was rejected."""

    def __init__(self, event: RejectConnection) -> None:
        super().__init__(
            f'Discord rejected the WebSocket connection - Error code {event.status_code}'
        )

        self.code = event.status_code
        self.headers = event.headers
