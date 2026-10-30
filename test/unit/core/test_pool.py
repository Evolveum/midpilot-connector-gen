# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
import socket

from src.core.pool import create_pool


def test_pool_worker_does_not_keep_server_sockets_open() -> None:
    """Closing a connection in the server must reach the peer while pool workers are alive.

    Regression: forked workers inherited the server's keep-alive connections, so Hypercorn's close sent
    no FIN and Gravitee's next poll on that connection timed out with a 504.
    """
    server_side, peer_side = socket.socketpair()
    executor = create_pool(1)
    try:
        assert executor.submit(os.getpid).result(timeout=30) != os.getpid()

        server_side.close()
        peer_side.settimeout(5)

        assert peer_side.recv(1) == b""
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        server_side.close()
        peer_side.close()
