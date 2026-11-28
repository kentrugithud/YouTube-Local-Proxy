"""
Локальный HTTP/HTTPS-прокси для системных настроек (Windows:
Параметры -> Сеть и Интернет -> Прокси-сервер).

Как работает:
  * браузер шлёт на прокси `CONNECT host:443`;
  * если host входит в список web_domains (по умолчанию YouTube),
    соединение туда заворачивается в WebSocket до вашего Cloudflare Worker
    (тот же worker, что и для Telegram: он открывает TCP до dst:443);
    шифрование TLS остаётся сквозным — браузер <-> youtube.com,
    Worker видит только шифротекст, а провайдер видит только *.workers.dev;
  * все остальные сайты идут напрямую, как будто прокси нет.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote

from .config import proxy_config
from .pool import cf_worker_pool
from .raw_websocket import RawWebSocket, set_sock_opts

log = logging.getLogger('tg-mtproto-proxy')

DEFAULT_WEB_DOMAINS: List[str] = [
    'youtube.com',
    'youtu.be',
    'youtube-nocookie.com',
    'youtubei.googleapis.com',
    'ytimg.com',
    'ggpht.com',
    'googlevideo.com',
]

MAX_HEAD = 64 * 1024
HEAD_TIMEOUT = 15.0
CONNECT_TIMEOUT = 10.0


def domain_matches(host: str, domains: Iterable[str]) -> bool:
    """host == d или host заканчивается на .d"""
    host = host.lower().rstrip('.')
    for d in domains:
        d = d.lower().strip().lstrip('.')
        if d and (host == d or host.endswith('.' + d)):
            return True
    return False


def parse_hostport(target: str, default_port: int) -> Tuple[str, int]:
    target = target.strip()
    if target.startswith('['):  # [ipv6]:port
        end = target.find(']')
        host = target[1:end]
        rest = target[end + 1:]
        port = int(rest[1:]) if rest.startswith(':') and rest[1:] else default_port
        return host, port
    if target.count(':') == 1:
        host, p = target.rsplit(':', 1)
        return host, int(p)
    return target, default_port


def build_pac(listen_host: str, port: int, domains: Iterable[str]) -> str:
    """PAC-скрипт: YouTube -> наш прокси, при его недоступности -> напрямую;
    всё остальное -> напрямую."""
    host = '127.0.0.1' if listen_host in ('', '0.0.0.0', '::') else listen_host
    doms = ', '.join('"%s"' % d.lower().strip().lstrip('.') for d in domains if d.strip())
    return (
        'function FindProxyForURL(url, host) {\n'
        '  host = host.toLowerCase();\n'
        '  var d = [%s];\n'
        '  for (var i = 0; i < d.length; i++) {\n'
        '    var s = d[i];\n'
        '    if (host === s || (host.length > s.length &&\n'
        '        host.substring(host.length - s.length - 1) === "." + s)) {\n'
        '      return "PROXY %s:%d; DIRECT";\n'
        '    }\n'
        '  }\n'
        '  return "DIRECT";\n'
        '}\n'
    ) % (doms, host, port)


async def _read_head(reader: asyncio.StreamReader) -> Tuple[bytes, bytes]:
    """Возвращает (заголовок запроса без \\r\\n\\r\\n, уже прочитанный хвост)."""
    buf = bytearray()
    while b'\r\n\r\n' not in buf:
        chunk = await reader.read(8192)
        if not chunk:
            raise ConnectionError('client closed before request head')
        buf.extend(chunk)
        if len(buf) > MAX_HEAD:
            raise ConnectionError('request head too large')
    head, _, rest = bytes(buf).partition(b'\r\n\r\n')
    return head, rest


async def _pump_tcp(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            if dst.can_write_eof():
                dst.write_eof()
        except Exception:
            pass


async def _pump_ws_up(reader: asyncio.StreamReader, ws: RawWebSocket):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send(data)
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass


async def _pump_ws_down(ws: RawWebSocket, writer: asyncio.StreamWriter):
    try:
        while True:
            data = await ws.recv()
            if data is None:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass


async def _open_worker_ws(host: str, port: int) -> Optional[RawWebSocket]:
    """Открыть WS до Worker'а, который соединится с host:443."""
    domains = list(proxy_config.cfproxy_worker_domains)
    if not domains or port != 443:
        return None
    path = f'/apiws?dst={quote(host, safe="")}'
    for worker in cf_worker_pool.available_domains(domains):
        try:
            return await RawWebSocket.connect(
                worker, worker, timeout=8, path=path,
                secure=not proxy_config.disable_secure)
        except Exception as exc:
            log.debug('web-proxy: worker %s for %s failed: %r',
                      worker, host, exc)
            cf_worker_pool.report_failure(worker, exc)
    return None


async def _tunnel_via_worker(ws, reader, writer):
    up = asyncio.create_task(_pump_ws_up(reader, ws))
    down = asyncio.create_task(_pump_ws_down(ws, writer))
    try:
        await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (up, down):
            t.cancel()
        await asyncio.gather(up, down, return_exceptions=True)
        await ws.close()


def _split_point(chunk: bytes, host: str) -> int:
    """Где разрезать TLS ClientHello: посередине имени сайта (SNI)."""
    idx = chunk.find(host.encode('ascii', 'ignore'))
    if idx > 0:
        return idx + max(1, len(host) // 2)
    return 2


async def _send_fragmented(remote_w: asyncio.StreamWriter, chunk: bytes, host: str):
    """Отправить первый TLS-пакет двумя частями, чтобы DPI не увидел SNI целиком."""
    if chunk[:1] == b'\x16' and len(chunk) > 4:  # TLS handshake
        cut = min(_split_point(chunk, host), len(chunk) - 1)
        remote_w.write(chunk[:cut])
        await remote_w.drain()
        await asyncio.sleep(0.02)
        remote_w.write(chunk[cut:])
    else:
        remote_w.write(chunk)
    await remote_w.drain()


async def _tunnel_direct(host, port, reader, writer, early: bytes = b''):
    r2, w2 = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT)
    set_sock_opts(w2.transport, proxy_config.buffer_size)
    return r2, w2


async def _handle_web_client(reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
    set_sock_opts(writer.transport, proxy_config.buffer_size)
    remote_w: Optional[asyncio.StreamWriter] = None
    try:
        head, rest = await asyncio.wait_for(_read_head(reader), HEAD_TIMEOUT)
        lines = head.split(b'\r\n')
        try:
            method, target, version = lines[0].decode('latin-1').split(' ', 2)
        except ValueError:
            writer.write(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            return

        # ---------- PAC-файл для автонастройки Windows ----------
        if method.upper() in ('GET', 'HEAD') and target.split('?')[0] == '/proxy.pac':
            body = build_pac(proxy_config.host, proxy_config.port,
                             proxy_config.web_domains or DEFAULT_WEB_DOMAINS
                             ).encode('utf-8')
            writer.write(
                b'HTTP/1.1 200 OK\r\n'
                b'Content-Type: application/x-ns-proxy-autoconfig\r\n'
                b'Cache-Control: no-cache\r\nConnection: close\r\n'
                + b'Content-Length: %d\r\n\r\n' % len(body)
                + (b'' if method.upper() == 'HEAD' else body))
            await writer.drain()
            return

        # ---------- HTTPS: CONNECT host:port ----------
        if method.upper() == 'CONNECT':
            host, port = parse_hostport(target, 443)
            matched = domain_matches(host, proxy_config.web_domains)
            via_worker = matched and proxy_config.connection_mode == 'worker'
            fragment = matched and proxy_config.connection_mode == 'direct'

            if via_worker:
                ws = await _open_worker_ws(host, port)
                if ws is not None:
                    log.info('[web] %s:%d -> Cloudflare Worker', host, port)
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    await writer.drain()
                    if rest:
                        await ws.send(rest)
                    await _tunnel_via_worker(ws, reader, writer)
                    return
                log.warning('[web] %s: worker недоступен, пробую напрямую', host)

            try:
                r2, remote_w = await _tunnel_direct(host, port, reader, writer)
            except Exception as exc:
                log.debug('[web] direct %s:%d failed: %r', host, port, exc)
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            log.debug('[web] %s:%d -> direct%s', host, port,
                      ' (fragmented)' if fragment else '')
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            if fragment:
                first = rest or await asyncio.wait_for(reader.read(65536), 15)
                if first:
                    await _send_fragmented(remote_w, first, host)
            elif rest:
                remote_w.write(rest)
                await remote_w.drain()
            t1 = asyncio.create_task(_pump_tcp(reader, remote_w))
            t2 = asyncio.create_task(_pump_tcp(r2, writer))
            try:
                await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (t1, t2):
                    t.cancel()
                await asyncio.gather(t1, t2, return_exceptions=True)
            return

        # ---------- обычный HTTP: GET http://host/path ----------
        if not target.lower().startswith('http://'):
            writer.write(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            return
        url_rest = target[7:]
        hostport, _, path = url_rest.partition('/')
        host, port = parse_hostport(hostport, 80)
        path = '/' + path

        new_head = [f'{method} {path} {version}'.encode('latin-1')]
        for ln in lines[1:]:
            low = ln.lower()
            if low.startswith((b'proxy-connection:', b'connection:',
                               b'proxy-authorization:')):
                continue
            new_head.append(ln)
        new_head.append(b'Connection: close')
        payload = b'\r\n'.join(new_head) + b'\r\n\r\n' + rest

        try:
            r2, remote_w = await _tunnel_direct(host, port, reader, writer)
        except Exception as exc:
            log.debug('[web] http %s:%d failed: %r', host, port, exc)
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            return
        remote_w.write(payload)
        await remote_w.drain()
        t1 = asyncio.create_task(_pump_tcp(reader, remote_w))
        t2 = asyncio.create_task(_pump_tcp(r2, writer))
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (t1, t2):
                t.cancel()
            await asyncio.gather(t1, t2, return_exceptions=True)

    except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError,
            asyncio.CancelledError):
        pass
    except Exception as exc:
        log.debug('[web] client error: %r', exc)
    finally:
        for w in (remote_w, writer):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass


async def start_web_proxy() -> Optional[asyncio.AbstractServer]:
    """Запускает локальный HTTP-прокси на proxy_config.host:port."""
    port = proxy_config.port
    if not proxy_config.web_domains:
        proxy_config.web_domains = list(DEFAULT_WEB_DOMAINS)
    server = await asyncio.start_server(
        _handle_web_client, proxy_config.host, port)
    log.info('  Web proxy:     HTTP %s:%d  (Windows -> Прокси-сервер)',
             proxy_config.host, port)
    log.info('  Web domains:   %s', ', '.join(proxy_config.web_domains))
    log.info('  Mode:          %s', proxy_config.connection_mode)
    if (proxy_config.connection_mode == 'worker'
            and not proxy_config.cfproxy_worker_domains):
        log.warning('  Web proxy: Cloudflare Worker не задан — YouTube будет '
                    'идти напрямую (без обхода). Укажите --cfproxy-worker-domain')
    return server
