import enum
from typing import Union

__all__ = (
    'Opcode',
    'CloseCode',
    'should_reconnect',
)


class Opcode(enum.IntEnum):
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    PRESENCE_UPDATE = 3
    VOICE_STATE_UPDATE = 4
    RESUME = 6
    RECONNECT = 7
    REQUEST_GUILD_MEMBERS = 8
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11


class CloseCode(enum.IntEnum):
    GENERIC_ERROR = 4000
    UNKNOWN_OPCODE = 4001
    DECODING_ERROR = 4002
    NOT_AUTHENTICATED = 4003
    AUTHENTICATION_FAILED = 4004
    ALREADT_AUTHENTICATED = 4005
    INVALID_SEQ = 4006
    RATE_LIMITED = 4008
    SESSION_TIMED_OUT = 4009
    INVALID_SHARD = 4010
    SHARDING_REQUIRED = 4011
    INVALID_API_VERSION = 4012
    INVALID_INTENTS = 4013
    DISALLOWED_INTENTS = 4014


# https://discord.com/developers/docs/topics/opcodes-and-status-codes#gateway-gateway-close-event-codes
RECONNECT_CLOSE_CODE = {
    CloseCode.GENERIC_ERROR: True,
    CloseCode.UNKNOWN_OPCODE: True,
    CloseCode.DECODING_ERROR: True,
    CloseCode.NOT_AUTHENTICATED: True,
    CloseCode.AUTHENTICATION_FAILED: False,
    CloseCode.ALREADT_AUTHENTICATED: True,
    CloseCode.INVALID_SEQ: True,
    CloseCode.RATE_LIMITED: True,
    CloseCode.SESSION_TIMED_OUT: True,
    CloseCode.INVALID_SHARD: False,
    CloseCode.SHARDING_REQUIRED: False,
    CloseCode.INVALID_API_VERSION: False,
    CloseCode.INVALID_INTENTS: False,
    CloseCode.DISALLOWED_INTENTS: False,
}


def should_reconnect(code: Union[int, CloseCode, None]) -> bool:
    """Utility function to determine if the connection should be reconnected.

    This function looks up the given code in a dictionary of known codes and
    recommendations for whether or not to reconnect.

    The implementation of this function is designed to be conservative in
    returning False, this means that when True is returned it may still not be
    completely safe to reconnect.

    Parameters:
        code:
            The close code to check for. If this is None then True will always
            be returned.

    Returns:
        Whether to reconnect to the gateway. False is used conservatively and
        if returned the gateway should **not** be reconnected.
    """
    if code is None:
        return True

    # For regular WebSocket close-codes, we can just assume we want to
    # reconnect as usual. This includes other unknown close codes such as those
    # in the 3000-3999 range.
    if 4000 > code > 5000:
        return True

    try:
        code = CloseCode(code)
    except ValueError:
        return True

    return RECONNECT_CLOSE_CODE.get(code, True)
