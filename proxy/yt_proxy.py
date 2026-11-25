"""YT-Local-Proxy: запуск локального HTTP-прокси для YouTube."""
from __future__ import annotations

import argparse
import asyncio
import logging

from .config import coerce_domain_list, proxy_config
from .web_proxy import start_web_proxy

log = logging.getLogger('tg-mtproto-proxy')


async def _run(stop_event: asyncio.Event | None = None) -> None:
    server = await start_web_proxy()
    if server is None:
        return
    stop_event = stop_event or asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    ap = argparse.ArgumentParser(description='YT-Local-Proxy')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--worker-domain', action='append', default=None,
                    metavar='DOMAIN', help='Cloudflare Worker (repeatable)')
    ap.add_argument('--web-domain', action='append', default=None,
                    metavar='DOMAIN', help='Extra domain routed via Worker')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s  %(levelname)-5s  %(message)s', datefmt='%H:%M:%S')
    proxy_config.host = args.host
    proxy_config.port = args.port
    proxy_config.cfproxy_worker_domains = coerce_domain_list(args.worker_domain)
    proxy_config.web_domains = coerce_domain_list(args.web_domain)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
