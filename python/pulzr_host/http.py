"""`pz.http`: HTTPS to the hosts a manifest declares, credentials filled in by
the host and never handed to the module."""

import base64
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT_S = 15
MAX_BODY = 4 * 1024 * 1024
MAX_REQUESTS = 512      # per call
MAX_BATCH = 128         # per array
BATCH_WORKERS = 24
MAX_CACHE_S = 3600
METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')

PLACEHOLDER = re.compile(
    r'\{\{(credential\.([a-z_][a-z0-9_]{0,63})|basic:([a-z_][a-z0-9_]{0,63}):([a-z_][a-z0-9_]{0,63}))\}\}')
_URL = re.compile(r'^https://([^/:?#@]+)(:443)?([/?#].*)?$')


class HttpError(Exception):
    """A request refused before it was made; answered to the module, not raised."""


class Cache:
    """A GET's answer kept for `cache` seconds, per install."""

    def get(self, key):
        raise NotImplementedError

    def put(self, key, value, ttl):
        raise NotImplementedError

    def drop(self, *keys):
        raise NotImplementedError


class MemoryCache(Cache):
    def __init__(self, clock=None):
        import time
        self._clock = clock or time.monotonic
        self._items = {}

    def get(self, key):
        item = self._items.get(key)
        if item is None or item[1] < self._clock():
            self._items.pop(key, None)
            return None
        return item[0]

    def put(self, key, value, ttl):
        self._items[key] = (value, self._clock() + ttl)

    def drop(self, *keys):
        for k in keys:
            self._items.pop(k, None)


def _substitute(text, creds, used):
    def one(m):
        if m.group(2):
            name = m.group(2)
            if name not in creds:
                raise HttpError(f'no credential {name}')
            used.add(str(creds[name]))
            return str(creds[name])
        user, password = m.group(3), m.group(4)
        if user not in creds or password not in creds:
            raise HttpError('no credential for that basic placeholder')
        used.update((str(creds[user]), str(creds[password])))
        return base64.b64encode(f'{creds[user]}:{creds[password]}'.encode()).decode()
    return PLACEHOLDER.sub(one, text)


def cache_key(namespace, url):
    return f'{namespace}http:{hashlib.sha256(url.encode()).hexdigest()}'


class Http:
    """One install's requests: `hosts` declared, `creds` its opened
    credentials for this call, `cache` per install under `namespace`."""

    def __init__(self, hosts, session=None, cache=None):
        self.hosts = {h.lower() for h in hosts}
        self.session = session or requests.Session()
        self.cache = cache

    def allowed(self, host):
        host = (host or '').lower()
        return any(host == h or (h.startswith('*.') and host.endswith(h[1:]) and host.count('.') >= h.count('.'))
                   for h in self.hosts)

    def perform(self, req, creds, namespace, meter):
        """One request object to its response object, never raising."""
        try:
            return self._perform(req if isinstance(req, dict) else {}, creds, namespace, meter)
        except HttpError as e:
            return {'status': 0, 'error': str(e)}

    def batch(self, reqs, creds, namespace, meter):
        with ThreadPoolExecutor(max_workers=min(BATCH_WORKERS, max(1, len(reqs)))) as pool:
            return list(pool.map(lambda r: self.perform(r, creds, namespace, meter), reqs))

    def _perform(self, req, creds, namespace, meter):
        method = str(req.get('method') or 'GET').upper()
        url = str(req.get('url') or '')
        m = _URL.match(url)
        if not m or not self.allowed(m.group(1)):
            raise HttpError(f'only {", ".join(sorted(self.hosts)) or "no host"}, over HTTPS')
        if PLACEHOLDER.search(url):
            raise HttpError('a credential may not go in a URL')
        if method not in METHODS:
            raise HttpError(f'{method} is not a request the host makes')
        ttl = req.get('cache') if method == 'GET' and isinstance(req.get('cache'), int) else 0
        key = cache_key(namespace, url)
        if ttl > 0 and self.cache is not None:
            held = self.cache.get(key)
            if isinstance(held, dict):
                meter['http_cached'] += 1
                return {**held, 'cached': True}
        used = set()
        headers = {str(k): _substitute(str(v), creds, used) for k, v in (req.get('headers') or {}).items()}
        body = req.get('body')
        data = _substitute(body, creds, used).encode() if isinstance(body, str) else None
        timeout = min(float(req.get('timeout_ms') or TIMEOUT_S * 1000) / 1000, TIMEOUT_S)
        meter['http_requests'] += 1
        meter['http_bytes_out'] += len(data or b'')
        try:
            r = self.session.request(method, url, headers=headers, data=data, timeout=timeout,
                                     allow_redirects=False, stream=True)
            raw = r.raw.read(MAX_BODY + 1, decode_content=True)
        except requests.Timeout:
            meter['http_timeouts'] += 1
            return {'status': 0, 'error': 'Timeout'}
        except requests.RequestException as e:
            return {'status': 0, 'error': type(e).__name__}
        meter['http_bytes_in'] += len(raw)
        if len(raw) > MAX_BODY:
            return {'status': 0, 'error': 'The response is too large.'}
        text = raw.decode('utf-8', errors='replace')
        for secret in used | {str(v) for v in creds.values()}:
            if len(secret) >= 4:
                text = text.replace(secret, '[credential]')
        out = {'status': r.status_code, 'headers': {k.lower(): v for k, v in r.headers.items()}, 'body': text}
        if ttl > 0 and self.cache is not None and r.status_code == 200:
            self.cache.put(key, out, min(ttl, MAX_CACHE_S))
        return out
