"""Small per-session HTTP/CONNECT proxy with numeric-IP dialing and bounded tunnels.

TLS stays end-to-end with Chrome's original hostname/certificate validation. This
is a narrow managed-Chrome adapter, not a general proxy or an OS network sandbox.
"""

import asyncio
from contextlib import suppress

from workbench.modules.browser.policy import NetworkPolicy, deny


class SessionProxy:
    def __init__(self, policy: NetworkPolicy):
        self.policy = policy
        self.server = None
        self.address: str | None = None
        self.tasks: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.bytes_used = 0
        self.blocked = 0
        self.closed = False

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0, limit=32768)
        self.address = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self.address

    async def close(self):
        self.closed = True
        self.address = None
        if self.server:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()
        current = asyncio.current_task()
        pending = [task for task in self.tasks if task != current]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.server:
            # Python 3.12 waits for accepted clients too. Close owned tunnels
            # before waiting, otherwise an idle keep-alive tunnel blocks stop.
            await self.server.wait_closed()

    async def _pipe(self, reader, writer):
        while data := await reader.read(65536):
            self.bytes_used += len(data)
            if self.bytes_used > 128 * 1024 * 1024 or self.closed:
                raise deny("浏览器会话已达到 128 MiB 网络预算。")
            writer.write(data)
            await writer.drain()

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        if self.closed or len(self.tasks) >= 24:
            writer.close()
            return
        self.tasks.add(task)
        self.writers.add(writer)
        upstream = None
        relays = []
        try:
            async with asyncio.timeout(120):
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                lines = header.decode("latin1").split("\r\n")
                method, target, protocol = lines[0].split(" ")
                if protocol != "HTTP/1.1" or method not in {
                    "CONNECT",
                    "GET",
                    "HEAD",
                    "POST",
                    "PUT",
                    "DELETE",
                    "PATCH",
                    "OPTIONS",
                }:
                    raise deny()
                if method == "CONNECT":
                    url = "https://" + target + "/"
                else:
                    url = target
                address, port = await self.policy.address(url)
                remote_reader, upstream = await asyncio.wait_for(
                    asyncio.open_connection(address, port), 10
                )
                self.writers.add(upstream)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    parsed = self.policy.validate(url)
                    # Only one plaintext request per connection: do not let a second
                    # absolute target reuse a connection that was authorized earlier.
                    headers = []
                    lengths = []
                    for line in lines[1:]:
                        if not line:
                            continue
                        name, value = line.split(":", 1)
                        name = name.lower().strip()
                        if name == "transfer-encoding" or name == "upgrade":
                            raise deny()
                        if name == "content-length":
                            lengths.append(int(value.strip()))
                        if name not in {
                            "host",
                            "proxy-authorization",
                            "proxy-connection",
                            "connection",
                        }:
                            headers.append(line)
                    if len(lengths) > 1 or any(
                        size < 0 or size > 2 * 1024 * 1024 for size in lengths
                    ):
                        raise deny()
                    first = f"{method} {parsed.raw_path.decode('ascii')} HTTP/1.1"
                    headers += [f"Host: {parsed.netloc.decode('ascii')}", "Connection: close"]
                    upstream.write(
                        (first + "\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("latin1")
                    )
                    if lengths and lengths[0]:
                        remaining = lengths[0]
                        while remaining:
                            chunk = await reader.readexactly(min(65536, remaining))
                            upstream.write(chunk)
                            remaining -= len(chunk)
                            self.bytes_used += len(chunk)
                            if self.bytes_used > 128 * 1024 * 1024:
                                raise deny()
                            await upstream.drain()
                    await upstream.drain()
                    await self._pipe(remote_reader, writer)
                    return
                relays = [
                    asyncio.create_task(self._pipe(reader, upstream)),
                    asyncio.create_task(self._pipe(remote_reader, writer)),
                ]
                done, _ = await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.blocked += 1
            # No target URLs, request headers, page contents or upstream errors logged.
            with suppress(Exception):
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
        finally:
            for relay in relays:
                relay.cancel()
            await asyncio.gather(*relays, return_exceptions=True)
            for owned in (upstream, writer):
                if owned:
                    self.writers.discard(owned)
                    owned.close()
                    with suppress(Exception):
                        await owned.wait_closed()
            self.tasks.discard(task)
