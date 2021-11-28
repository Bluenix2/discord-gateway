# Discord gateway

Sans-I/O Python implementation of the Discord gateway.

Sans-I/O means that this implements no I/O (network) and operates purely on the
bytes given using `wsproto`.

It means that this implementation can be reused for libraries implemented in a
threading fashion or asyncio/trio/curio.

## Usage

For a reference implementation see
[wumpy-gateway](https://github.com/Bluenix2/wumpy/library/wumpy-gateway/wumpy/gateway/shard.py).
It is designed to handle all sort of network failures and race conditions.

## Quickstart

Here's a very minimal implementation using the `socket` library and `threading`
for the heartbeating (it does not handle reconnecting or any form of
unexpected disconnections):

```python
import socket
import ssl
import threading
import time
from sys import platform

import certifi
from discord_gateway import DiscordConnection


TOKEN = 'ABC123.XYZ789'
RECV_SIZE = 65536
SERVER_NAME = 'gateway.discord.gg'


def heartbeat(conn, sock):
    while True:
        sock.send(conn.heartbeat())
        time.sleep(conn.heartbeat_interval)


def recv_event(conn, sock):
    while True:
        for event in conn.events():
            return event

        for to_send in conn.receive(sock.recv(RECV_SIZE)):
            sock.send(to_send)


def main():
    # Setup the socket and SSL for the WebSocket Secure connection.
    conn = DiscordConnection('gateway.discord.gg', encoding='json')
    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(conn.destination)
    sock = ctx.wrap_socket(sock, server_hostname=SERVER_NAME)

    sock.send(conn.connect())  # Convert to a WebSocket

    # Receive the very first HELLO event.
    hello = recv_event(conn, sock)

    # Send RESUME or IDENTIFY depending on state (will always be False
    # when initially connecting, but may be different when reconnecting).
    if conn.should_resume:
        sock.send(conn.resume(TOKEN))
    else:
        sock.send(conn.identify(
            token=TOKEN,
            intents=65535,
            properties={
                '$os': platform,
                '$browser': 'discord-gateway',
                '$device': 'discord-gateway'
            },
        ))

    heartbeater = threading.Thread(target=heartbeat, args=(conn,sock))
    heartbeater.start()

    try:
        while True:
            event = recv_event(conn, sock)
            print('Received:', event)
    finally:
        sock.shutdown(socket.SHUT_WR)
        sock.close()

if __name__ == '__main__':
    main()
```
