"""Where an install's `state` lives. The platform keeps it in its database;
`pulzr-pkg test` and the tests keep it in memory."""

import threading


class StateStore:
    """Per install, a few small values by key, `limit` octets in all."""

    def get(self, install, key):
        """The value (bytes) or None."""
        raise NotImplementedError

    def set(self, install, key, value, limit):
        """Store `value` (None deletes). False, storing nothing, when the
        install would hold more than `limit` octets of keys and values."""
        raise NotImplementedError


class MemoryState(StateStore):
    def __init__(self):
        self._lock = threading.Lock()
        self.values = {}   # install -> {key: value}

    def get(self, install, key):
        with self._lock:
            return self.values.get(install, {}).get(key)

    def set(self, install, key, value, limit):
        with self._lock:
            mine = dict(self.values.get(install, {}))
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
            if sum(len(k) + len(v) for k, v in mine.items()) > limit:
                return False
            self.values[install] = mine
            return True
