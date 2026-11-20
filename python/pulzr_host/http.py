"""`pz.http`: HTTPS to the hosts a manifest declares, nothing else."""

import fnmatch
import urllib.parse

import requests

TIMEOUT_MS = 10_000
MAX_BODY = 1 << 20


class HttpError(Exception):
    """A request the host would not make, or one that failed."""


def allowed(hosts, host):
    host = (host or '').lower()
    return any(host == h or (h.startswith('*.') and fnmatch.fnmatch(host, h) and host.count('.') >= h.count('.'))
               for h in hosts)


class HttpClient:
    """Makes a module's requests: HTTPS only, to `hosts` only (each a name, or
    `*.` and a name), no redirects, a response body of at most MAX_BODY."""

    def __init__(self, hosts, session=None):
        self.hosts = [h.lower() for h in hosts]
        self._session = session or requests.Session()

    def request(self, method, url, headers, body, timeout_ms=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https':
            raise HttpError('https only')
        if parts.username or parts.password or not allowed(self.hosts, parts.hostname):
            raise HttpError(f'{parts.hostname} is not a declared host')
        timeout = min(int(timeout_ms or TIMEOUT_MS), TIMEOUT_MS) / 1000
        try:
            with self._session.request(method, url, headers=headers, data=body or None, timeout=timeout,
                                       allow_redirects=False, stream=True) as r:
                data = r.raw.read(MAX_BODY + 1, decode_content=True)
                if len(data) > MAX_BODY:
                    raise HttpError('the response is larger than 1 MiB')
                return r.status_code, dict(r.headers), data
        except requests.RequestException as e:
            raise HttpError(f'{type(e).__name__}: {str(e)[:200]}') from None
