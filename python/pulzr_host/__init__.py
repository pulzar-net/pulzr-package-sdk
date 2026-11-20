"""The host side of the extension ABI (docs/abi.md): load a module, run one
instance of it per install, and lend it the host functions its manifest
declares. Every service that runs extension modules uses this, so a module
that passes `pulzr-pkg test` runs the same way in the platform.

    host = Host()
    module = host.load(wasm, manifest_module, name='example 1.0.0')
    instance = module.instance(install='org-a', state=store, http=client, credentials={'api': '...'})
    answer = instance.call({'event': 'tick', 'now': 1795000000.0})

What a kind's events mean, which install an event belongs to and which of an
answer's parts are taken is the caller's: this runs a module and nothing else.
"""

import base64
import json
import logging
import threading
import time

import wasmtime

from .http import HttpClient, HttpError
from .state import MemoryState, StateStore

__all__ = ['Host', 'Module', 'Instance', 'LoadError', 'HttpClient', 'HttpError', 'MemoryState',
           'StateStore', 'ABI_VERSIONS', 'MAX_FUEL']

log = logging.getLogger('pulzr_host')

ABI_VERSIONS = (1, 2)
# About 65 ms of an Apple M1 core spent to the last unit (docs/abi.md, "Cost").
MAX_FUEL = 100_000_000
MAX_MEMORY_MIB = 256
DEFAULT_FUEL = 50_000_000
DEFAULT_MEMORY_MIB = 32
DEFAULT_STATE_KIB = 64
MAX_STATE_KIB = 1024
MAX_KEY = 64
BREAKER_FAILURES = 5
BREAKER_SECONDS = 60.0
MAX_HTTP_PER_CALL = 8

I32, I64, F64 = wasmtime.ValType.i32(), wasmtime.ValType.i64(), wasmtime.ValType.f64()

# What each declared import lends, and its signature.
IMPORTS = {
    'log': {'log': ([I32, I32, I32], [])},
    'now': {'now': ([], [F64])},
    'state': {'state_get': ([I32, I32, I32, I32], [I32]), 'state_set': ([I32, I32, I32, I32], [I32])},
    'http': {'http': ([I32, I32], [I64])},
}
EXPORTS = ('memory', 'pz_abi', 'pz_alloc', 'pz_handle')
LEVELS = (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)


class LoadError(ValueError):
    """A module that cannot be run, worded for its author."""


def _sig(t):
    return ([str(p) for p in t.params], [str(r) for r in t.results])


class Host:
    """One wasmtime engine, with fuel metering, for every module it loads."""

    def __init__(self, max_fuel=MAX_FUEL):
        config = wasmtime.Config()
        config.consume_fuel = True
        self.engine = wasmtime.Engine(config)
        self.max_fuel = max_fuel

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
        self.credentials = {c['name']: c['inject'] for c in spec.get('credentials') or []}

    def instance(self, install, state=None, http=None, credentials=None, clock=time.time,
                 monotonic=time.monotonic):
        """One install's instance. `state` keeps its state (a StateStore; a
        MemoryState by default), `http` makes its requests (an HttpClient
        limited to the declared hosts by default), `credentials` maps a
        declared credential's name to its secret, or is a function of the name."""
        if http is None and 'http' in self.imported:
            http = HttpClient(self.hosts)
        return Instance(self, install, state if state is not None else MemoryState(),
                        http, credentials or {}, clock, monotonic)


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
        self._http_calls = 0

    # -- the host functions -----------------------------------------------------------

    def _linker(self, store):
        linker = wasmtime.Linker(self.module.host.engine)
        imported = self.module.imported

        def text(caller, ptr, length):
            return caller['memory'].read(caller, ptr, ptr + length).decode('utf-8', 'replace')

        def raw(caller, ptr, length):
            return bytes(caller['memory'].read(caller, ptr, ptr + length))

        def pz_log(caller, level, ptr, length):
            log.log(LEVELS[max(0, min(level, 3))], '%s: %s', self.name, text(caller, ptr, min(length, 4096)))

        def pz_now():
            return float(self._clock())

        def pz_state_get(caller, kp, kl, out, cap):
            if kl > MAX_KEY:
                return -1
            value = self._state.get(self.install, raw(caller, kp, kl))
            if value is None:
                return -1
            if len(value) <= cap:
                caller['memory'].write(caller, value, out)
            return len(value)

        def pz_state_set(caller, kp, kl, vp, vl):
            if kl > MAX_KEY:
                return -1
            key = raw(caller, kp, kl)
            value = raw(caller, vp, vl)
            return 0 if self._state.set(self.install, key, value or None, self.module.state_bytes) else -1

        def pz_http(caller, ptr, length):
            self._http_calls += 1
            if self._http_calls > MAX_HTTP_PER_CALL:
                answer = {'error': f'more than {MAX_HTTP_PER_CALL} requests in one call'}
            else:
                answer = self._request(raw(caller, ptr, length))
            data = json.dumps(answer, separators=(',', ':')).encode()
            at = caller['pz_alloc'](caller, len(data))
            caller['memory'].write(caller, data, at)
            return (at << 32) | len(data)

        funcs = {'log': (pz_log, True), 'now': (pz_now, False), 'state_get': (pz_state_get, True),
                 'state_set': (pz_state_set, True), 'http': (pz_http, True)}
        for group in IMPORTS.values():
            for fname, (params, results) in group.items():
                if fname in imported:
                    fn, caller = funcs[fname]
                    linker.define_func('pz', fname, wasmtime.FuncType(params, results), fn,
                                       access_caller=caller)
        return linker

    def _request(self, body):
        try:
            req = json.loads(body)
            if not isinstance(req, dict):
                raise ValueError('a request is an object')
        except ValueError as e:
            return {'error': f'not a request: {e}'}
        headers = dict(req.get('headers') or {})
        name = req.get('credential')
        if name is not None:
            inject = self.module.credentials.get(name)
            secret = self._credentials(name) if callable(self._credentials) else self._credentials.get(name)
            if inject is None or secret is None:
                return {'error': f'no credential {name}'}
            value = inject['format'].replace('{secret_b64}', base64.b64encode(secret.encode()).decode()) \
                .replace('{secret}', secret)
            headers = {k: v for k, v in headers.items() if k.lower() != inject['header'].lower()}
            headers[inject['header']] = value
        try:
            payload = base64.b64decode(req.get('body') or '', validate=True)
        except ValueError:
            return {'error': 'body is not base64'}
        try:
            status, out_headers, out_body = self._http.request(
                str(req.get('method') or 'GET'), str(req.get('url') or ''), headers, payload,
                req.get('timeout_ms'))
        except HttpError as e:
            return {'error': str(e)}
        return {'status': status, 'headers': out_headers, 'body': base64.b64encode(out_body).decode()}

    # -- calls --------------------------------------------------------------------------

    def _make(self):
        store = wasmtime.Store(self.module.host.engine)
        store.set_limits(memory_size=self.module.memory)
        # A start function and pz_abi run on fuel too.
        store.set_fuel(self.module.fuel)
        instance = self._linker(store).instantiate(store, self.module.compiled)
        exports = instance.exports(store)
        if exports['pz_abi'](store) not in ABI_VERSIONS:
            raise LoadError(f'{self.name}: pz_abi is not one of {ABI_VERSIONS}')
        self._store, self._exports = store, exports

    def call(self, event):
        """The module's answer to one event (a dict), or None after any
        failure: logged by the event's kind, never its payload, and the
        instance made again at the next call."""
        data = json.dumps(event, separators=(',', ':')).encode()
        with self._lock:
            if self._monotonic() < self.resting_until:
                return None
            try:
                if self._store is None:
                    self._make()
                store, ex = self._store, self._exports
                store.set_fuel(self.module.fuel)
                self._http_calls = 0
                memory = ex['memory']
                ptr = ex['pz_alloc'](store, len(data))
                memory.write(store, data, ptr)
                packed = ex['pz_handle'](store, ptr, len(data)) & 0xffff_ffff_ffff_ffff
                start, length = packed >> 32, packed & 0xffff_ffff
                answer = json.loads(memory.read(store, start, start + length))
                if not isinstance(answer, dict):
                    raise ValueError('the answer is not an object')
                self._failures = 0
                return answer
            except Exception as e:  # a trap, the limits, a bad answer
                first = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
                log.warning('%s: %s event failed (%s); the instance is made again',
                            self.name, event.get('event'), first)
                self._store = self._exports = None
                self._failures += 1
                if self._failures >= BREAKER_FAILURES:
                    self.resting_until = self._monotonic() + BREAKER_SECONDS
                    self._failures = 0
                    log.error('%s: %d failures in a row; resting %ds', self.name, BREAKER_FAILURES,
                              BREAKER_SECONDS)
                return None

    @property
    def store(self):
        return self._store

    @property
    def exports(self):
        return self._exports
