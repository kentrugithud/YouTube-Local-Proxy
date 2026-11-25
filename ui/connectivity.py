from __future__ import annotations

import base64
import os
import socket as _socket
import ssl
import struct
from contextlib import nullcontext
from tkinter import messagebox
from typing import Any, Optional

from proxy.utils import create_ssl_context
from ui.i18n import t

TEST_HOST = "www.youtube.com"


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _ws_send(sock, payload: bytes) -> None:
    n = len(payload)
    header = bytearray([0x82])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _ws_recv(sock) -> Optional[bytes]:
    while True:
        b0, b1 = _recv_exact(sock, 2)
        opcode = b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        payload = _recv_exact(sock, n) if n else b""
        if opcode == 8:
            return None
        if opcode in (9, 10):
            continue
        return payload


def _test_worker(domain: str, *, secure: bool = True) -> Any:
    """Открывает через Worker TLS-соединение до www.youtube.com.
    Возвращает True или текст ошибки."""
    ctx = create_ssl_context() if secure else None
    port = 443 if secure else 80
    try:
        with _socket.create_connection((domain, port), timeout=8) as raw:
            raw.settimeout(10)
            connection = (ctx.wrap_socket(raw, server_hostname=domain)
                          if secure else nullcontext(raw))
            with connection as ws:
                key = base64.b64encode(os.urandom(16)).decode()
                ws.sendall((
                    f"GET /apiws?dst={TEST_HOST} HTTP/1.1\r\n"
                    f"Host: {domain}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "Sec-WebSocket-Protocol: binary\r\n\r\n"
                ).encode())
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = ws.recv(512)
                    if not chunk:
                        break
                    buf += chunk
                first = buf.decode("utf-8", "replace").split("\r\n")[0]
                if "101" not in first:
                    return first or t("connectivity.no_response")

                # полноценный TLS-handshake с youtube через туннель
                incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
                tls = create_ssl_context().wrap_bio(
                    incoming, outgoing, server_hostname=TEST_HOST)
                while True:
                    try:
                        tls.do_handshake()
                        break
                    except ssl.SSLWantReadError:
                        data = outgoing.read()
                        if data:
                            _ws_send(ws, data)
                        chunk = _ws_recv(ws)
                        if chunk is None:
                            return t("connectivity.worker_closed")
                        incoming.write(chunk)
                return True
    except _socket.timeout:
        return t("connectivity.timeout")
    except (OSError, ssl.SSLError, ConnectionError) as exc:
        msg = str(exc)
        return msg[:80] if len(msg) > 80 else msg


def run_cfworker_multi_test(domains: list, *, secure: bool = True) -> dict:
    return {domain: _test_worker(domain, secure=secure) for domain in domains}


def show_multi_connectivity_results(per_domain: dict, *, parent: Any) -> None:
    blocks = []
    all_ok = True
    for domain, result in per_domain.items():
        if result is True:
            blocks.append(t("connectivity.domain_ok", domain=domain))
        else:
            all_ok = False
            blocks.append(t("connectivity.domain_fail", domain=domain, error=result))
    title = t("connectivity.title_ok") if all_ok else t("connectivity.title_fail")
    messagebox.showinfo(title, "\n\n".join(blocks), parent=parent)
