"""Bounded public HTTP capture with DNS addresses pinned to the socket.

The injection points are transport and DNS resolver, so security can be tested
without relaxing production networking. No cookies, proxies, or credentials.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit, urljoin
from urllib.robotparser import RobotFileParser
import zlib


_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='intake-dns')
_DNS_SLOTS = threading.BoundedSemaphore(4)
_FETCH_SLOTS = threading.BoundedSemaphore(4)
USER_AGENT = 'AI-Library-Source-Intake/1.0'


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: dict
    body: bytes


class FetchError(ValueError):
    def __init__(self, message, code='FETCH_REJECTED'):
        super().__init__(message)
        self.code = code


def normalize_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise FetchError('URL must be nonempty and at most 4096 characters')
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise FetchError('URL contains unsafe characters')
    parts = urlsplit(value)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        raise FetchError('Only public HTTP(S) URLs are supported')
    if parts.username is not None or parts.password is not None:
        raise FetchError('URL credentials are forbidden')
    try:
        port = parts.port
        hostname = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    except (ValueError, UnicodeError) as exc:
        raise FetchError('Invalid URL hostname or port') from exc
    scheme = parts.scheme.lower()
    if port not in (None, 80 if scheme == 'http' else 443):
        raise FetchError('Only the standard HTTP(S) port is allowed')
    if '%' in hostname or len(hostname) > 253 or not hostname:
        raise FetchError('Invalid hostname')
    authority = '[' + hostname + ']' if ':' in hostname else hostname
    return urlunsplit((scheme, authority, parts.path or '/', parts.query, ''))


def _resolve(host, port):
    return list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


def _public_address(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise FetchError('DNS returned an invalid address') from exc
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise FetchError('Private, local, reserved and metadata network targets are forbidden', 'SSRF_REJECTED')
    return str(ip)


def _transport(url, address, timeout, limit):
    """Connect to the verified literal IP; TLS still authenticates the hostname."""
    parts = urlsplit(url)
    deadline = time.monotonic() + timeout
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    connection = http.client.HTTPConnection(parts.hostname, 443 if parts.scheme == 'https' else 80, timeout=timeout)
    try:
        sock.connect((address, connection.port))
        if parts.scheme == 'https':
            sock.settimeout(max(.001, deadline - time.monotonic()))
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        connection.sock = sock
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request('GET', path, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,text/markdown',
                                                'Accept-Encoding': 'identity', 'Connection': 'close'})
        sock.settimeout(max(.001, deadline - time.monotonic()))
        response = connection.getresponse()
        headers = response.getheaders()
        if sum(len(k) + len(v) for k, v in headers) > 65536:
            raise FetchError('Response headers exceed the limit', 'RESOURCE_LIMIT')
        normalized = {key.lower(): value for key, value in headers}
        length = normalized.get('content-length')
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise FetchError('Response content length exceeds the limit', 'RESOURCE_LIMIT')
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('Total capture timeout exceeded', 'FETCH_TIMEOUT')
            sock.settimeout(remaining)
            chunk = response.read1(min(65536, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > limit:
                raise FetchError('Response exceeds the byte limit', 'RESOURCE_LIMIT')
        return FetchResponse(response.status, normalized, bytes(body))
    finally:
        connection.close()
        sock.close()


class SafeWebFetcher:
    def __init__(self, *, resolver=None, transport=None, max_bytes=2 * 1024 * 1024,
                 timeout=20.0, max_redirects=5, min_host_interval=.1):
        self.resolver = resolver or _resolve
        self.transport = transport or _transport
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.min_host_interval = min_host_interval
        self._host_times = {}
        self._rate_lock = threading.Lock()

    def _target(self, url, deadline):
        parts = urlsplit(url)
        if not _DNS_SLOTS.acquire(timeout=max(0, deadline - time.monotonic())):
            raise FetchError('DNS concurrency limit reached', 'RESOURCE_LIMIT')
        future = _DNS_POOL.submit(self.resolver, parts.hostname, 443 if parts.scheme == 'https' else 80)
        future.add_done_callback(lambda _: _DNS_SLOTS.release())
        try:
            addresses = future.result(timeout=max(.001, deadline - time.monotonic()))
        except FutureTimeout as exc:
            future.cancel()
            raise FetchError('DNS lookup timed out', 'FETCH_TIMEOUT') from exc
        except OSError as exc:
            raise FetchError('DNS lookup failed', 'DNS_FAILED') from exc
        if not addresses:
            raise FetchError('DNS returned no targets', 'DNS_FAILED')
        validated = [_public_address(address) for address in addresses]
        # Every DNS answer is validated, and the socket uses this exact IP.
        return validated[0]

    def _rate_limit(self, host, deadline):
        with self._rate_lock:
            now = time.monotonic()
            allowed = max(now, self._host_times.get(host, 0))
            if allowed >= deadline:
                raise FetchError('Rate limit exceeds capture deadline', 'FETCH_TIMEOUT')
            self._host_times[host] = allowed + self.min_host_interval
        delay = allowed - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _request(self, url, deadline):
        address = self._target(url, deadline)
        self._rate_limit(urlsplit(url).hostname, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError('Total capture timeout exceeded', 'FETCH_TIMEOUT')
        try:
            response = self.transport(url, address, remaining, self.max_bytes)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError('Network capture failed', 'FETCH_FAILED') from exc
        if time.monotonic() > deadline:
            raise FetchError('Total capture timeout exceeded', 'FETCH_TIMEOUT')
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        if len(response.body) > self.max_bytes:
            raise FetchError('Response exceeds byte limit', 'RESOURCE_LIMIT')
        body = response.body
        encoding = headers.get('content-encoding', 'identity').lower()
        if encoding not in ('identity', 'gzip', 'deflate', ''):
            raise FetchError('Unsupported response compression', 'UNSUPPORTED_ENCODING')
        if encoding in ('gzip', 'deflate'):
            try:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS)
                body = decoder.decompress(body, self.max_bytes + 1)
                if len(body) > self.max_bytes or decoder.unconsumed_tail:
                    raise FetchError('Decompressed response exceeds the limit', 'RESOURCE_LIMIT')
                body += decoder.flush(self.max_bytes + 1 - len(body))
                if len(body) > self.max_bytes or not decoder.eof or decoder.unused_data:
                    raise FetchError('Invalid or oversized compressed response', 'RESOURCE_LIMIT')
            except zlib.error as exc:
                raise FetchError('Invalid compressed response', 'PARSE_FAILED') from exc
        return FetchResponse(response.status, headers, body)

    def _robots(self, url, deadline):
        origin = urlsplit(url)
        robots_url = urlunsplit((origin.scheme, origin.netloc, '/robots.txt', '', ''))
        current = robots_url
        for _ in range(self.max_redirects + 1):
            response = self._request(current, deadline)
            if response.status in (301, 302, 303, 307, 308):
                current = normalize_url(urljoin(current, response.headers.get('location', '')))
                continue
            if response.status in (404, 410):
                return
            if response.status != 200:
                raise FetchError('Source access rules unavailable or access denied', 'ACCESS_RULES_DENIED')
            parser = RobotFileParser()
            parser.parse(response.body.decode('utf-8', errors='replace').splitlines())
            if not parser.can_fetch(USER_AGENT, url):
                raise FetchError('Source disallows automated capture', 'ACCESS_RULES_DENIED')
            delay = parser.crawl_delay(USER_AGENT)
            if delay:
                with self._rate_lock:
                    self._host_times[origin.hostname] = max(self._host_times.get(origin.hostname, 0), time.monotonic() + delay)
            return
        raise FetchError('Too many access-rule redirects', 'REDIRECT_LIMIT')

    def fetch(self, url, *, check_robots=True):
        requested = normalize_url(url)
        deadline = time.monotonic() + self.timeout
        if not _FETCH_SLOTS.acquire(timeout=self.timeout):
            raise FetchError('Capture concurrency limit reached', 'RESOURCE_LIMIT')
        try:
            current = requested
            checked_origins = set()
            for _ in range(self.max_redirects + 1):
                origin = urlsplit(current)
                access_key = (origin.scheme, origin.netloc, origin.path)
                if check_robots and access_key not in checked_origins:
                    self._robots(current, deadline)
                    checked_origins.add(access_key)
                response = self._request(current, deadline)
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise FetchError('Redirect has no target', 'FETCH_FAILED')
                    current = normalize_url(urljoin(current, location))
                    continue
                if response.status != 200:
                    code = 'RATE_LIMITED' if response.status == 429 else 'HTTP_ERROR'
                    raise FetchError(f'Source returned HTTP {response.status}; no login or access bypass is attempted', code)
                mime = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                if mime not in ('text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown'):
                    raise FetchError('Only HTML and text webpages are supported', 'UNSUPPORTED_FORMAT')
                return {'body': response.body, 'requested_url': requested, 'final_url': current,
                        'http_status': response.status, 'mime_type': mime, 'published_at': None,
                        'charset': _charset(response.headers.get('content-type', '')),
                        'retention_policy': 'bounded_original_response_and_selected_text'}
            raise FetchError('Too many redirects', 'REDIRECT_LIMIT')
        finally:
            _FETCH_SLOTS.release()


def _charset(content_type):
    for field in content_type.split(';')[1:]:
        if field.strip().lower().startswith('charset='):
            return field.split('=', 1)[1].strip().strip('\"\'')
    return 'utf-8'
