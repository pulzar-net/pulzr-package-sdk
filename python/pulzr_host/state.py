"""Where an install's `state` lives. adapter-ingest keeps it in net-db, the
connectivity service in Redis; `pulzr-pkg test` and the tests in memory."""

import threading
import time


class StateStore:
    """Per install, a few small values by key, `limit` octets in all, each
    for `ttl` seconds or until replaced (`ttl` 0)."""

    def get(self, install, key):
        """The value (bytes) or None."""
        raise NotImplementedError

    def set(self, install, key, value, limit, ttl=0):
        """Store `value` (None deletes). False, storing nothing, when the
        install would hold more than `limit` octets of keys and values."""
        raise NotImplementedError

    def size(self, install):
        """Octets the install holds now, keys and values."""
        raise NotImplementedError


class MemoryState(StateStore):
    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self.values = {}    # install -> {key: value}
        self._expires = {}  # (install, key) -> when

    def _live(self, install):
        now = self._clock()
        mine = self.values.get(install, {})
        for key in [k for k in mine if self._expires.get((install, k), now + 1) <= now]:
            del mine[key]
            self._expires.pop((install, key), None)
        return mine

    def get(self, install, key):
        with self._lock:
            return self._live(install).get(key)

    def set(self, install, key, value, limit, ttl=0):
        with self._lock:
            mine = dict(self._live(install))
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
            if sum(len(k) + len(v) for k, v in mine.items()) > limit:
                return False
            self.values[install] = mine
            if ttl and value is not None:
                self._expires[(install, key)] = self._clock() + ttl
            else:
                self._expires.pop((install, key), None)
            return True

    def size(self, install):
        with self._lock:
            return sum(len(k) + len(v) for k, v in self._live(install).items())
