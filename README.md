# Discord gateway

Sans-I/O Python implementation of the Discord gateway.

Sans-I/O means that this implements no I/O (network) and operates purely on the
bytes given using `wsproto`.

It means that this implementation can be reused for libraries implemented in a
threading fashion or asyncio/trio/curio.

## Reference Implementation

For a reference implementation see
[wumpy-gateway](https://github.com/Bluenix2/wumpy/blob/main/library/wumpy-gateway/wumpy/gateway/shard.py).
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


TOKEN = 'YOUR_VERY.WELL.HIDDEN_TOKEN'
RECV_SIZE = 65536
SERVER_NAME = 'wss://gateway.discord.gg/'


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
    conn = DiscordConnection(SERVER_NAME, encoding='json')
    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(conn.destination)
    sock = ctx.wrap_socket(sock, server_hostname=conn.destination[0])

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

## Comprehensive Guide

### Connecting

The very first thing required to use the Discord Gateway is to connect using a
TCP socket with TLS enabled.

Start by creating an instance of `DiscordConnection`, passing in the URI and
encoding to use (most likely `'json'`), assign it to a variable called `conn`.

Now, connect a TCP socket to `conn.destination` (this is a property that
returns a tuple with the host and port to use - as often used when connecting)
and wrap it with TLS. Using the built-in `socket` and `ssl` module with
`certifi` that looks like this:

```python
SERVER_NAME = 'wss://gateway.discord.gg/'


conn = DiscordConnection(SERVER_NAME, encoding='json')
sock = socket.create_connection(conn.destination)

ctx = ssl.create_default_context(cafile=certifi.where())
sock = ctx.wrap_socket(sock, server_hostname=conn.destination[0])
```

After the connection is established, generate a HTTP 101 Switching Protocols
request by calling `conn.connect()` and sending it over the socket.

### Bootstrapping and HELLOs

It is now time to communicate over the WebSocket - or almost!

You have yet to receive anything over the socket, not even the response to the
HTTP request made. Create a while-loop that will iterate until a full event
has been received, but it is important to wrap it in a try/except statement
that catches the `ConnectionRejected` in-case Discord rejects the WebSocket
upgrade.

In each iteration the data from the socket should be passed to
`conn.receive()`, which then returns a list of bytes that should be sent back
to the socket. The code should roughly look like this:

```python
hello = None
while hello is None:
    try:
        for to_send in conn.receive(sock.recv(65535)):
            sock.send(to_send)
    except ConnectionRejected:
        print('Discord rejected the connection!')
        raise

    for hello in conn.events():
        # After this has been executed, the hello variable will no longer
        # be set to None and the while-loop will exit.
        break
```

When this loop has exited all internal state of discord-gateway should be ready
to IDENTIFY or RESUME depending on `conn.should_resume` (which is always
falsely during startup - but useful during reconnections).

### Heartbeating

The next part is maintaining the connection established, this is done by
launching a concurrent thread/task (depending on how concurrency is handled).

The only purpose of this concurrent heartbeater is to periodically generate
a HEARTBEAT commands and sleep:

```python
def heartbeater(conn: DiscordConnection, sock: socket.socket) -> typing.NoReturn:
    # Discord recommends sleeping this random amount of time before the first
    # heartbeat, this is to relieve Discord's servers when they are starting
    # up again after downtimes.
    time.sleep(random.random() * conn.heartbeat_interval)

    while True:
        sock.send(conn.heartbeat())
        time.sleep(conn.heartbeat_interval)
```

After this, you are now fully connected and can receive events similar to how
you received the HELLO event.

### Disconnecting

#### Expected Disconnections

When disconnecting you need to follow the WebSocket protocol, to start off
generate a closing frame using `conn.close()` and send it over the socket.

Afterwards, receive data until discord-gateway raises `CloseDiscordConnection`
and send the `data` attribute over the socket. Lastly shutdown the write end
of the socket using `sock.shutdown(socket.SHUT_WR)` and then fully close the
socket. In code that would look like this:

```python
sock.send(conn.close())

try:
    while True:
        for data in conn.receive(socket.recv(65535)):
            sock.send(data)
except CloseDiscordConnection as err:
    if err.data is not None:
        sock.send(err.data)

# Shutdown the socket now that the WebSocket is closed
sock.shutdown(socket.SHUT_WR)

received = None
while received != b'':
    received = sock.recv(65535)

sock.close()
```

#### Unexpected Disconnections

Discord-gateway automatically schedules for reconnections in cases such as
un-acknowledged heartbeats and RECONNECT events. That said, discord-gateway
still needs your help to actually reconnect the underlying socket.

Like with expected disconnections discord-gateway raises
`CloseDiscordConnection` when the closing handshake is being made, just follow
what was done for expected disconnects and then reconnect but do not create a
new `DiscordConnection` instance. Doing so looses information such as why the
reconnection is happening in the first place. Instead call the `reconnect()`
method to reset the internal state.

`reconnect()` returns an integer representing the amount of seconds to wait
before retrying, this will expotentially increase until a successful HELLO
event is received. To take advantage of this, you should structure a retry loop
like this:

```python
while True:
    time.sleep(conn.reconnect())

    try:
        # Connect to the socket as normal, see "Bootstrapping and HELLOs" above
        ...
    except EOFError:
        # Note that in some implementations an empty bytes object is returned
        # to signal this. It essentially means that the socket closed. Don't
        # forget to clean up the socket in all of these except-clauses.
        ...
        continue

    except ConnectionRejected as err:
        ...
        continue
    else:
        # If the bootstrapping succeeded and we received a HELLO event, then
        # we can finally exit this loop.
        break
```

Use the `should_reconnect()` function to determine whether to reconnect, if it
returns `False` that signals that the connection **must not** be reconnected.
It is recommended to raise an error or similar in this case.

### Race conditions

Because of the concurrent heartbeater there are potential race conditions,
primarily when reconnecting.

The easiest way to fix this is to use a lock that is held when reconnecting,
the heartbeater has to acquire it everytime it tries to send a heartbeat.

Another potential issue is sending a heartbeat while the closing handshake is
in progress - that is, the client has sent one closing frame but not yet
received it from the other end. Discord-gateway provides a simple property
called `closed`, if this is set just skip sending anything and sleep again.

Following the theme of `socket`s and threads here is an example of an improved
heartbeater:

```python
lock = ...


def heartbeater(conn, sock):
    # Discord recommends sleeping this random amount of time before the first
    # heartbeat, this is to relieve Discord's servers when they are starting
    # up again after downtimes.
    time.sleep(random.random() * conn.heartbeat_interval)

    while True:
        with lock:
            if not conn.closing:
                sock.send(conn.heartbeat())
        time.sleep(conn.heartbeat_interval)
```
