import asyncio
import unittest
from unittest import mock

from proxy import web_proxy
from proxy.config import proxy_config
from proxy.web_proxy import domain_matches, parse_hostport, _handle_web_client


class FakeWs:
    """Имитирует Worker: пересылает байты на локальный TCP-сервер."""
    def __init__(self, r, w):
        self.r, self.w, self.closed = r, w, False
    async def send(self, data):
        self.w.write(data.upper()); await self.w.drain()
    async def recv(self):
        d = await self.r.read(65536)
        return d or None
    async def close(self):
        self.w.close()


class DomainTests(unittest.TestCase):
    def test_match(self):
        d = ['youtube.com', 'googlevideo.com']
        self.assertTrue(domain_matches('www.youtube.com', d))
        self.assertTrue(domain_matches('rr1---sn-abc.googlevideo.com', d))
        self.assertTrue(domain_matches('YOUTUBE.com.', d))
        self.assertFalse(domain_matches('notyoutube.com', d))
        self.assertFalse(domain_matches('example.com', d))

    def test_hostport(self):
        self.assertEqual(parse_hostport('a.com:8443', 443), ('a.com', 8443))
        self.assertEqual(parse_hostport('a.com', 443), ('a.com', 443))
        self.assertEqual(parse_hostport('[::1]:80', 443), ('::1', 80))


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def echo(r, w):
            while d := await r.read(4096):
                w.write(d); await w.drain()
            w.close()
        self.echo = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.echo_port = self.echo.sockets[0].getsockname()[1]
        self.web = await asyncio.start_server(_handle_web_client, '127.0.0.1', 0)
        self.web_port = self.web.sockets[0].getsockname()[1]
        proxy_config.web_domains = ['youtube.com']
        proxy_config.cfproxy_worker_domains = ['w.example.workers.dev']

    async def asyncTearDown(self):
        self.echo.close(); self.web.close()

    async def test_connect_direct(self):
        r, w = await asyncio.open_connection('127.0.0.1', self.web_port)
        w.write(f'CONNECT 127.0.0.1:{self.echo_port} HTTP/1.1\r\n\r\n'.encode())
        self.assertIn(b'200', await r.readline())
        await r.readline()
        w.write(b'hello'); await w.drain()
        self.assertEqual(await asyncio.wait_for(r.readexactly(5), 3), b'hello')
        w.close()

    async def test_connect_via_worker(self):
        async def fake_open(host, port):
            self.assertEqual((host, port), ('www.youtube.com', 443))
            r, w = await asyncio.open_connection('127.0.0.1', self.echo_port)
            return FakeWs(r, w)
        with mock.patch.object(web_proxy, '_open_worker_ws', fake_open):
            r, w = await asyncio.open_connection('127.0.0.1', self.web_port)
            w.write(b'CONNECT www.youtube.com:443 HTTP/1.1\r\nHost: x\r\n\r\n')
            self.assertIn(b'200', await r.readline())
            await r.readline()
            w.write(b'abc'); await w.drain()
            # FakeWs.send делает upper(), эхо возвращает как есть
            self.assertEqual(await asyncio.wait_for(r.readexactly(3), 3), b'ABC')
            w.close()

    async def test_plain_http(self):
        async def http(r, w):
            head = await r.readuntil(b'\r\n\r\n')
            self.assertTrue(head.startswith(b'GET /p?x=1 HTTP/1.1'))
            self.assertNotIn(b'Proxy-Connection', head)
            w.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok'); await w.drain()
            w.close()
        srv = await asyncio.start_server(http, '127.0.0.1', 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection('127.0.0.1', self.web_port)
        w.write(f'GET http://127.0.0.1:{port}/p?x=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                f'Proxy-Connection: keep-alive\r\n\r\n'.encode())
        data = await asyncio.wait_for(r.read(), 3)
        self.assertTrue(data.endswith(b'ok'))
        srv.close()

    async def test_worker_down_falls_back_direct(self):
        async def none(host, port): return None
        with mock.patch.object(web_proxy, '_open_worker_ws', none):
            r, w = await asyncio.open_connection('127.0.0.1', self.web_port)
            w.write(b'CONNECT www.youtube.com:1 HTTP/1.1\r\n\r\n')
            self.assertIn(b'502', await asyncio.wait_for(r.readline(), 12))


if __name__ == '__main__':
    unittest.main()


import shutil
import subprocess


class PacTests(unittest.IsolatedAsyncioTestCase):
    async def test_pac_served(self):
        proxy_config.host = '127.0.0.1'; proxy_config.port = 8080
        proxy_config.web_domains = ['youtube.com']
        srv = await asyncio.start_server(_handle_web_client, '127.0.0.1', 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection('127.0.0.1', port)
        w.write(b'GET /proxy.pac HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
        data = await asyncio.wait_for(r.read(), 3)
        srv.close()
        self.assertIn(b'200 OK', data)
        self.assertIn(b'application/x-ns-proxy-autoconfig', data)
        self.assertIn(b'FindProxyForURL', data)
        self.assertIn(b'PROXY 127.0.0.1:8080; DIRECT', data)

    @unittest.skipUnless(shutil.which('node'), 'node не установлен')
    def test_pac_logic_in_javascript(self):
        pac = web_proxy.build_pac('0.0.0.0', 8080, ['youtube.com', 'googlevideo.com'])
        script = pac + '''
        const cases = {
          "www.youtube.com": "PROXY 127.0.0.1:8080; DIRECT",
          "youtube.com": "PROXY 127.0.0.1:8080; DIRECT",
          "rr1.sn-x.googlevideo.com": "PROXY 127.0.0.1:8080; DIRECT",
          "WWW.YouTube.com": "PROXY 127.0.0.1:8080; DIRECT",
          "notyoutube.com": "DIRECT",
          "example.com": "DIRECT",
          "youtube.com.evil.org": "DIRECT",
        };
        for (const [h, want] of Object.entries(cases)) {
          const got = FindProxyForURL("https://" + h + "/", h);
          if (got !== want) { console.log("FAIL " + h + " -> " + got); process.exit(1); }
        }
        console.log("OK");
        '''
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'OK', out.stdout + out.stderr)


class FragmentTests(unittest.IsolatedAsyncioTestCase):
    def test_split_point_inside_sni(self):
        hello = b'\x16\x03\x01\x02\x00' + b'x' * 20 + b'www.youtube.com' + b'y' * 30
        cut = web_proxy._split_point(hello, 'www.youtube.com')
        sni = hello.index(b'www.youtube.com')
        self.assertTrue(sni < cut < sni + len('www.youtube.com'))
        self.assertEqual(web_proxy._split_point(b'\x16\x03\x01', 'nothere.example'), 2)

    async def test_direct_mode_splits_first_tls_packet(self):
        received = []
        done = asyncio.Event()

        async def server(r, w):
            first = await r.read(65536)
            received.append(first)
            await asyncio.sleep(0.15)
            try:
                rest = await asyncio.wait_for(r.read(65536), 0.2)
            except asyncio.TimeoutError:
                rest = b''
            received.append(rest)
            done.set()
            w.close()

        srv = await asyncio.start_server(server, '127.0.0.1', 0)
        sport = srv.sockets[0].getsockname()[1]
        proxy_config.web_domains = ['localhost']
        proxy_config.connection_mode = 'direct'
        web = await asyncio.start_server(_handle_web_client, '127.0.0.1', 0)
        wport = web.sockets[0].getsockname()[1]
        try:
            r, w = await asyncio.open_connection('127.0.0.1', wport)
            w.write(f'CONNECT localhost:{sport} HTTP/1.1\r\n\r\n'.encode())
            await r.readline(); await r.readline()
            hello = b'\x16\x03\x01\x02\x00' + b'a' * 10 + b'localhost' + b'b' * 40
            w.write(hello); await w.drain()
            await asyncio.wait_for(done.wait(), 3)
            self.assertEqual(b''.join(received), hello)      # ничего не потеряно
            self.assertLess(len(received[0]), len(hello))    # а пришло двумя частями
            self.assertGreater(len(received[1]), 0)
            w.close()
        finally:
            proxy_config.connection_mode = 'worker'
            srv.close(); web.close()
