#!/usr/bin/env python3
"""Fixed-code, bounded-output hostname resolver for the WSS socket adapter."""

from __future__ import annotations

import json
import socket
import sys

MAX_ADDRESSES = 64
ALLOWED_HOSTS = {"mainnet.helius-rpc.com"}


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    host = sys.argv[1]
    if (
        not host
        or len(host) > 253
        or not host.isascii()
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
            for c in host
        )
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
        or host.lower() not in ALLOWED_HOSTS
    ):
        return 2
    try:
        records = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError:
        print('{"status":"error","addresses":[]}', end="")
        return 0
    addresses = []
    for family, socktype, _proto, _canonname, sockaddr in records:
        if family not in {socket.AF_INET, socket.AF_INET6} or socktype != socket.SOCK_STREAM:
            continue
        ip = sockaddr[0]
        scope_id = sockaddr[3] if family == socket.AF_INET6 else 0
        addresses.append({"family": family, "ip": ip, "scope_id": scope_id})
        if len(addresses) > MAX_ADDRESSES:
            print('{"status":"error","addresses":[]}', end="")
            return 0
    print(json.dumps({"status": "ok", "addresses": addresses}, separators=(",", ":")), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
