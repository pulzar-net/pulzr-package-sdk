"""The host side of the extension ABI (docs/abi.md): load a module, run one
instance of it per install, lend it the host functions its manifest declares,
and meter what it costs. Every service that runs extension modules uses this,
so a module that passes `pulzr-pkg test` runs the same way in the platform.

    host = Host(max_fuel=100_000_000)
    module = host.load(wasm, manifest_module, name='acme-gateway 1.0.0')
    instance = module.instance('org-a/acme-gateway', state=store, cache=cache)
    answer = instance.call({'event': 'tick', 'now': 1795000000.0}, credentials={'api_key': '...'})
    host.usage.drain()   # per install, module and day, for the caller's metering

What a kind's events mean, which install an event belongs to and which of an
answer's parts are taken is the caller's: this runs a module and nothing else.
"""

import json
import logging
import threading
import time
from collections import Counter

import wasmtime

from .http import Cache, Http, HttpError, MemoryCache, cache_key, MAX_BATCH, MAX_REQUESTS
from .state import MemoryState, StateStore

__all__ = ['Host', 'Module', 'Instance', 'LoadError', 'Cache', 'MemoryCache', 'HttpError', 'MemoryState',
           'StateStore', 'Usage', 'ABI_VERSIONS', 'MAX_FUEL']

log = logging.getLogger('pulzr_host')

ABI_VERSIONS = (1, 2)
# A service's own cap may be lower (adapter-ingest's is 100 million, about
# 65 ms of an Apple M1 core spent to the last unit; docs/abi.md, "Cost").
MAX_FUEL = 2_000_000_000
MAX_MEMORY_MIB = 256
DEFAULT_FUEL = 50_000_000
DEFAULT_MEMORY_MIB = 32
DEFAULT_STATE_KIB = 64
MAX_STATE_KIB = 1024
MAX_KEY = 64
MAX_TTL_S = 30 * 86400
BREAKER_FAILURES = 5
BREAKER_SECONDS = 60.0
MAX_ANSWER = 4 * 1024 * 1024

I32, I64, F64 = wasmtime.ValType.i32(), wasmtime.ValType.i64(), wasmtime.ValType.f64()

# What each declared import lends, and its signature.
IMPORTS = {
    'log': {'log': ([I32, I32, I32], [])},
    'now': {'now': ([], [F64])},
    'state': {'state_get': ([I32, I32, I32, I32], [I32]), 'state_set': ([I32, I32, I32, I32, I32], [I32])},
    'http': {'http': ([I32, I32], [I32]), 'http_take': ([I32], [])},
}
EXPORTS = ('memory', 'pz_abi', 'pz_alloc', 'pz_handle')
LEVELS = (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
FIELDS = ('calls', 'fuel', 'cpu_ms', 'traps', 'timeouts', 'peak_memory', 'http_requests', 'http_cached', 'http_timeouts',
          'http_bytes_out', 'http_bytes_in', 'state_bytes')


class LoadError(ValueError):
    """A module that cannot be run, worded for its author."""


def _sig(t):
    return ([str(p) for p in t.params], [str(r) for r in t.results])


class Usage:
    """What modules cost, per (install, module, UTC day): calls, fuel used, CPU
    milliseconds, traps (and of them timeouts: fuel run out), the peak of linear memory, HTTP requests made, answered
    from cache and timed out, octets sent and received, and the state held at
    the day's last call. `drain()` hands the totals to the caller's metering."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._rows = {}

    def add(self, install, module, counts):
        day = time.strftime('%Y-%m-%d', time.gmtime(self._clock()))
        with self._lock:
            row = self._rows.setdefault((install, module, day), Counter())
            for k, v in counts.items():
                if k in ('peak_memory', 'state_bytes'):
                    row[k] = max(row[k], v) if k == 'peak_memory' else v
                else:
                    row[k] += v

    def drain(self):
        """[(install, module, day, {field: value})], and nothing kept."""
        with self._lock:
            rows, self._rows = self._rows, {}
        return [(i, m, d, {f: c.get(f, 0) for f in FIELDS}) for (i, m, d), c in rows.items()]


class Host:
    """One wasmtime engine, with fuel metering, for every module it loads."""

    def __init__(self, max_fuel=MAX_FUEL, clock=time.time):
        config = wasmtime.Config()
        config.consume_fuel = True
        self.engine = wasmtime.Engine(config)
        self.max_fuel = max_fuel
        self.usage = Usage(clock)

    def load(self, wasm, spec, name='module'):
        """A compiled module, checked against its manifest's module object
        `spec`: the exports the ABI needs, and no import it did not declare."""
        try:
            compiled = wasmtime.Module(self.engine, wasm)
        except wasmtime.WasmtimeError as e:
            raise LoadError(f'{name}: not a WebAssembly module: {str(e).splitlines()[0]}') from None
        declared = set(spec.get('imports') or ())
        allowed = {f: sig for d in declared for f, sig in IMPORTS.get(d, {}).items()}
        for imp in compiled.imports:
            if imp.module != 'pz' or imp.name not in allowed:
                raise LoadError(f'{name}: imports {imp.module}.{imp.name}, which its manifest does not declare')
            want = ([str(t) for t in allowed[imp.name][0]], [str(t) for t in allowed[imp.name][1]])
            if not isinstance(imp.type, wasmtime.FuncType) or _sig(imp.type) != want:
                raise LoadError(f'{name}: pz.{imp.name} has the wrong signature')
        exported = {e.name for e in compiled.exports}
        missing = [e for e in EXPORTS if e not in exported]
        if missing:
            raise LoadError(f'{name}: does not export {", ".join(missing)}')
        return Module(self, compiled, spec, name, {i.name for i in compiled.imports})


class Module:
    def __init__(self, host, compiled, spec, name, imported):
        self.host, self.compiled, self.spec, self.name = host, compiled, spec, name
        self.imported = imported
        limits = spec.get('limits') or {}
        self.fuel = min(int(limits.get('fuel') or DEFAULT_FUEL), host.max_fuel)
        self.memory = min(int(limits.get('memoryMiB') or DEFAULT_MEMORY_MIB), MAX_MEMORY_MIB) << 20
        self.state_bytes = min(int(limits.get('stateKiB') or DEFAULT_STATE_KIB), MAX_STATE_KIB) << 10
        self.hosts = list((spec.get('http') or {}).get('hosts') or [])

    def instance(self, install, state=None, cache=None, session=None, credentials=None, clock=time.time,
                 monotonic=time.monotonic):
        """One install's instance. `state` keeps its state (a StateStore; a
        MemoryState by default) and `cache` its cached GETs (a Cache);
        `credentials` are its opened credentials, by field, unless a call
        brings its own."""
        http = Http(self.hosts, session=session, cache=cache if cache is not None else MemoryCache()) \
            if 'http' in self.imported else None
        return Instance(self, install, state if state is not None else MemoryState(clock), http,
                        credentials or {}, clock, monotonic)


class Instance:
    """A module's instance for one install, made again after any failure."""

    def __init__(self, module, install, state, http, credentials, clock, monotonic):
        self.module, self.install = module, install
        self.name = f'{module.name} for {install}'
        self._state, self._http, self._credentials = state, http, credentials
        self._clock, self._monotonic = clock, monotonic
        self._lock = threading.Lock()
        self._store = self._exports = None
        self._failures = 0
        self.resting_until = 0.0
        self._call = None   # this call's credentials, meter and pending http answer

    # -- the host functions -----------------------------------------------------------

    def _read(self, caller, ptr, length):
        memory = caller['memory']
        if length < 0 or ptr < 0 or ptr + length > memory.data_len(caller):
            raise wasmtime.Trap('outside the module\'s memory')
        return bytes(memory.read(caller, ptr, ptr + length))

    def _linker(self):
        linker = wasmtime.Linker(self.module.host.engine)
        imported = self.module.imported

        def pz_log(caller, level, ptr, length):
            text = self._read(caller, ptr, min(length, 4096)).decode('utf-8', 'replace')
            log.log(LEVELS[max(0, min(level, 3))], '%s: %s', self.name, text)

        def pz_now():
            return float(self._clock())

        def pz_state_get(caller, kp, kl, out, cap):
            if not 0 < kl <= MAX_KEY:
                return -1
            value = self._state.get(self.install, self._read(caller, kp, kl))
            if value is None:
                return -1
            if len(value) <= cap:
                caller['memory'].write(caller, value, out)
            return len(value)

        def pz_state_set(caller, kp, kl, vp, vl, ttl):
            if not 0 < kl <= MAX_KEY or ttl < 0:
                return -1
            value = self._read(caller, vp, vl) or None
            ok = self._state.set(self.install, self._read(caller, kp, kl), value, self.module.state_bytes,
                                 min(ttl, MAX_TTL_S))
            return 0 if ok else -1

        def pz_http(caller, ptr, length):
            call = self._call
            try:
                req = json.loads(self._read(caller, ptr, length))
            except ValueError:
                call['pending'] = json.dumps({'status': 0, 'error': 'The request is not JSON.'}).encode()
                return len(call['pending'])
            batch = req if isinstance(req, list) else [req]
            call['requests'] += len(batch)
            if call['requests'] > MAX_REQUESTS or len(batch) > MAX_BATCH:
                call['pending'] = None
                return -1
            creds = call['credentials']
            if isinstance(req, list):
                out = self._http.batch(batch, creds, self.install + ':', call['meter'])
            else:
                out = self._http.perform(req, creds, self.install + ':', call['meter'])
            call['pending'] = json.dumps(out).encode()
            return len(call['pending'])

        def pz_http_take(caller, ptr):
            data = self._call.get('pending') or b''
            memory = caller['memory']
            if ptr < 0 or ptr + len(data) > memory.data_len(caller):
                raise wasmtime.Trap('http_take outside the module\'s memory')
            memory.write(caller, data, ptr)
            self._call['pending'] = None

        funcs = {'log': pz_log, 'now': pz_now, 'state_get': pz_state_get, 'state_set': pz_state_set,
                 'http': pz_http, 'http_take': pz_http_take}
        for group in IMPORTS.values():
            for fname, (params, results) in group.items():
                if fname in imported:
                    linker.define_func('pz', fname, wasmtime.FuncType(params, results), funcs[fname],
                                       access_caller=fname != 'now')
        return linker

    # -- calls --------------------------------------------------------------------------

    def _make(self):
        store = wasmtime.Store(self.module.host.engine)
        store.set_limits(memory_size=self.module.memory)
        # A start function and pz_abi run on fuel too.
        store.set_fuel(self.module.fuel)
        instance = self._linker().instantiate(store, self.module.compiled)
        exports = instance.exports(store)
        if exports['pz_abi'](store) not in ABI_VERSIONS:
            raise LoadError(f'{self.name}: pz_abi is not one of {ABI_VERSIONS}')
        self._store, self._exports = store, exports

    def call(self, event, credentials=None):
        """The module's answer to one event (a dict), or None after any
        failure: logged by the event's kind, never its payload, and the
        instance made again at the next call. `credentials` (opened, by
        field) fill this call's `pz.http` placeholders."""
        data = json.dumps(event, separators=(',', ':')).encode()
        with self._lock:
            if self._monotonic() < self.resting_until:
                return None
            meter = Counter(calls=1)
            self._call = {'credentials': dict(credentials if credentials is not None else self._credentials),
                          'requests': 0, 'pending': None, 'meter': meter}
            started = time.perf_counter()
            answer = None
            try:
                if self._store is None:
                    self._make()
                store, ex = self._store, self._exports
                store.set_fuel(self.module.fuel)
                memory = ex['memory']
                ptr = ex['pz_alloc'](store, len(data))
                memory.write(store, data, ptr)
                packed = ex['pz_handle'](store, ptr, len(data)) & 0xffff_ffff_ffff_ffff
                start, length = packed >> 32, packed & 0xffff_ffff
                if length > MAX_ANSWER or start + length > memory.data_len(store):
                    raise ValueError('the answer is outside its memory')
                answer = json.loads(memory.read(store, start, start + length))
                if not isinstance(answer, dict):
                    raise ValueError('the answer is not an object')
                self._failures = 0
                meter['fuel'] = self.module.fuel - store.get_fuel()
                meter['peak_memory'] = memory.data_len(store)
                invalidate = answer.get('invalidate')
                if self._http is not None and self._http.cache is not None and isinstance(invalidate, list):
                    self._http.cache.drop(*[cache_key(self.install + ':', str(u)) for u in invalidate[:256]])
                return answer
            except Exception as e:  # a trap, the limits, a bad answer
                first = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
                log.warning('%s: %s event failed (%s); the instance is made again',
                            self.name, event.get('event') or event.get('op'), first)
                meter['traps'] = 1
                if 'fuel' in str(e):
                    meter['timeouts'] = 1   # ran its fuel out: the call's time limit
                if self._store is not None:
                    try:
                        meter['fuel'] = self.module.fuel - self._store.get_fuel()
                        meter['peak_memory'] = self._exports['memory'].data_len(self._store)
                    except Exception:
                        meter['fuel'] = self.module.fuel
                self._store = self._exports = None
                self._failures += 1
                if self._failures >= BREAKER_FAILURES:
                    self.resting_until = self._monotonic() + BREAKER_SECONDS
                    self._failures = 0
                    log.error('%s: %d failures in a row; resting %ds', self.name, BREAKER_FAILURES,
                              BREAKER_SECONDS)
                return None
            finally:
                meter['cpu_ms'] = int((time.perf_counter() - started) * 1000)
                if 'state' in self.module.imported:
                    meter['state_bytes'] = self._state.size(self.install)
                self.module.host.usage.add(self.install, self.module.name, meter)
                self._call = None

    @property
    def store(self):
        return self._store

    @property
    def exports(self):
        return self._exports
