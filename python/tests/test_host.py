"""The host against small modules written by hand in the WebAssembly text
format, one per thing under test."""

import json
import logging

import pytest
import wasmtime

from pulzr_host import BREAKER_FAILURES, MAX_FUEL, Host, HttpError, LoadError, MemoryState


def wat(body='', imports='', data='', answer='{}', alloc=None, abi=2):
    """A module answering `answer` from its data, after running `body`."""
    alloc = alloc or '''(global $bump (mut i32) (i32.const 32768))
      (func (export "pz_alloc") (param $n i32) (result i32) (local $p i32)
        global.get $bump local.set $p
        global.get $bump local.get $n i32.add global.set $bump local.get $p)'''
    esc = answer.replace('\\', '\\\\').replace('"', '\\"')
    return wasmtime.wat2wasm(f'''(module {imports}
      (memory (export "memory") 1)
      (data (i32.const 1024) "{esc}")
      {data}
      {alloc}
      (func (export "pz_abi") (result i32) i32.const {abi})
      (func (export "pz_handle") (param i32 i32) (result i64)
        {body}
        i64.const 1024 i64.const 32 i64.shl i64.const {len(answer.encode())} i64.or))''')


@pytest.fixture
def host():
    return Host()


def test_a_module_that_imports_nothing(host):
    m = host.load(wat(answer='{"publish":[]}'), {})
    assert m.instance('org-a').call({'event': 'tick', 'now': 0}) == {'publish': []}
    # ABI 1 is run the same way.
    assert host.load(wat(abi=1), {}).instance('x').call({'event': 'tick'}) == {}


def test_what_cannot_load(host):
    log_import = '(import "pz" "log" (func (param i32 i32 i32)))'
    with pytest.raises(LoadError, match='does not declare'):
        host.load(wat(imports=log_import), {})
    with pytest.raises(LoadError, match='does not declare'):
        host.load(wat(imports='(import "env" "x" (func))'), {'imports': ['log']})
    with pytest.raises(LoadError, match='wrong signature'):
        host.load(wat(imports='(import "pz" "log" (func (param i32)))'), {'imports': ['log']})
    with pytest.raises(LoadError, match='does not export'):
        host.load(wasmtime.wat2wasm('(module (memory (export "memory") 1))'), {})
    with pytest.raises(LoadError, match='not a WebAssembly'):
        host.load(b'nonsense', {})
    bad_abi = host.load(wat(abi=9), {}).instance('x')
    assert bad_abi.call({'event': 'tick'}) is None


def test_log_and_now(host, caplog):
    imports = '''(import "pz" "log" (func $log (param i32 i32 i32)))
                 (import "pz" "now" (func $now (result f64)))'''
    data = '(data (i32.const 2048) "hello")'
    body = '''i32.const 1 i32.const 2048 i32.const 5 call $log
              call $now f64.const 42 f64.ne if unreachable end'''
    m = host.load(wat(body, imports, data), {'imports': ['log', 'now']}, name='ex')
    with caplog.at_level(logging.INFO, logger='pulzr_host'):
        assert m.instance('org-a', clock=lambda: 42.0).call({'event': 'tick'}) == {}
    assert 'ex for org-a: hello' in caplog.text
    assert m.instance('org-a', clock=lambda: 41.0).call({'event': 'tick'}) is None, 'now was not lent'


STATE = """(import "pz" "state_get" (func $get (param i32 i32 i32 i32) (result i32)))
           (import "pz" "state_set" (func $set (param i32 i32 i32 i32 i32) (result i32)))"""


def test_state_per_install_its_quota_and_ttl(host):
    data = '(data (i32.const 2048) "k") (data (i32.const 2056) "v") (data (i32.const 2064) "t")'
    # Read k: on a first call set it, and t for 60 s; on a later one k must be "v".
    body = """(if (i32.eq (call $get (i32.const 2048) (i32.const 1) (i32.const 3000) (i32.const 8)) (i32.const -1))
                (then (drop (call $set (i32.const 2048) (i32.const 1) (i32.const 2056) (i32.const 1) (i32.const 0)))
                      (drop (call $set (i32.const 2064) (i32.const 1) (i32.const 2056) (i32.const 1) (i32.const 60))))
                (else (if (i32.ne (i32.load8_u (i32.const 3000)) (i32.const 118)) (then unreachable))))
              ;; 2000 octets is past a 1 KiB quota: refused.
              (if (i32.ne (call $set (i32.const 2048) (i32.const 1) (i32.const 4000) (i32.const 2000) (i32.const 0))
                          (i32.const -1))
                (then unreachable))"""
    m = host.load(wat(body, STATE, data), {'imports': ['state'], 'limits': {'stateKiB': 1}})
    clock = [1000.0]
    store = MemoryState(clock=lambda: clock[0])
    a, b = m.instance('org-a', state=store), m.instance('org-b', state=store)
    assert a.call({'event': 'tick'}) == {} and a.call({'event': 'tick'}) == {}
    assert store.values == {'org-a': {b'k': b'v', b't': b'v'}}
    assert b.call({'event': 'tick'}) == {} and store.values['org-b'] == {b'k': b'v', b't': b'v'}, 'one install each'
    clock[0] += 61
    assert store.get('org-a', b't') is None and store.get('org-a', b'k') == b'v', 'the ttl'
    # It survives the instance: a restart keeps state.
    assert m.instance('org-a', state=store).call({'event': 'tick'}) == {}
    usage = {i: c for i, _m, _d, c in host.usage.drain()}
    assert usage['org-a']['state_bytes'] == 2 and usage['org-b']['state_bytes'] == 4


class FakeSession:
    def __init__(self, body=b'pong s3cret'):
        self.requests, self.body = [], body

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=True, stream=False):
        assert allow_redirects is False
        self.requests.append((method, url, headers, data))
        body = self.body

        class Raw:
            def read(self, n, decode_content=True):
                return body

        class R:
            status_code = 200
            raw = Raw()
            headers = {'Content-Type': 'text/plain'}
        return R()


def http_module(host, request, hosts=('api.example.com',)):
    """A module that makes `request` (an object or an array) and answers the
    response, read back with http_take."""
    req = json.dumps(request)
    esc = req.replace('\\', '\\\\').replace('"', '\\"')
    source = f"""(module
      (import "pz" "http" (func $http (param i32 i32) (result i32)))
      (import "pz" "http_take" (func $take (param i32)))
      (memory (export "memory") 1)
      (data (i32.const 1024) "{esc}")
      (func (export "pz_alloc") (param i32) (result i32) i32.const 32768)
      (func (export "pz_abi") (result i32) i32.const 2)
      (func (export "pz_handle") (param i32 i32) (result i64) (local $n i32)
        (local.set $n (call $http (i32.const 1024) (i32.const {len(req.encode())})))
        (call $take (i32.const 16384))
        i64.const 16384 i64.const 32 i64.shl local.get $n i64.extend_i32_u i64.or))"""
    spec = {'imports': ['http'], 'http': {'hosts': list(hosts)},
            'credentials': [{'name': 'api_key', 'label': 'API key', 'secret': True}]}
    return host.load(wasmtime.wat2wasm(source), spec)


def test_http_fills_credentials_and_hides_them(host):
    session = FakeSession()
    m = http_module(host, {'method': 'POST', 'url': 'https://api.example.com/v1/ping',
                           'headers': {'Authorization': 'Bearer {{credential.api_key}}'},
                           'body': '{"key": "{{credential.api_key}}"}'})
    answer = m.instance('org-a', session=session).call({'event': 'tick'}, credentials={'api_key': 's3cret'})
    method, url, headers, data = session.requests[0]
    assert (method, url, headers, data) == ('POST', 'https://api.example.com/v1/ping',
                                            {'Authorization': 'Bearer s3cret'}, b'{"key": "s3cret"}')
    assert answer == {'status': 200, 'headers': {'content-type': 'text/plain'}, 'body': 'pong [credential]'}, \
        'an echoed secret is hidden from the module'
    # Another organization's own, or none.
    m.instance('org-b', session=session).call({'event': 'tick'}, credentials={'api_key': 'other'})
    assert session.requests[1][2] == {'Authorization': 'Bearer other'}
    assert m.instance('org-c', session=session).call({'event': 'tick'}) == \
        {'status': 0, 'error': 'no credential api_key'}


def test_http_refuses_what_is_not_declared(host):
    session = FakeSession()
    for req in ({'url': 'https://evil.example.org/'}, {'url': 'http://api.example.com/'},
                {'url': 'https://api.example.com/{{credential.api_key}}'},
                {'method': 'TRACE', 'url': 'https://api.example.com/'},
                {'url': 'https://user@api.example.com/'}):
        answer = http_module(host, req).instance('x', session=session).call({'event': 'tick'},
                                                                              credentials={'api_key': 'k'})
        assert answer['status'] == 0 and answer['error'], req
    assert not session.requests


def test_http_batches_and_caches(host):
    session = FakeSession(b'{}')
    reqs = [{'url': f'https://api.example.com/sims/{i}', 'cache': 60} for i in range(3)]
    inst = http_module(host, reqs).instance('org-a', session=session)
    # The module answers the array itself, which is not an answer object: the
    # requests are what is under test here.
    inst.call({'event': 'tick'})
    inst.call({'event': 'tick'})
    assert len(session.requests) == 3, 'the second batch was answered from the cache'
    usage = {r[0]: r[3] for r in host.usage.drain()}
    assert usage['org-a']['http_requests'] == 3 and usage['org-a']['http_cached'] == 3
    assert usage['org-a']['calls'] == 2 and usage['org-a']['traps'] == 2


def test_usage_is_metered_per_install_and_day(host):
    m = host.load(wat(), {})
    m.instance('org-a').call({'event': 'tick'})
    m.instance('org-a').call({'event': 'tick'})
    m.instance('org-b').call({'event': 'tick'})
    looping = host.load(wat('(loop $l (br $l))'), {'limits': {'fuel': 1_000_000}}, name='loop')
    looping.instance('org-a').call({'event': 'tick'})
    rows = {(i, n): c for i, n, _day, c in host.usage.drain()}
    assert rows[('org-a', 'module')]['calls'] == 2 and rows[('org-b', 'module')]['calls'] == 1
    assert rows[('org-a', 'module')]['peak_memory'] == 65536
    assert rows[('org-a', 'loop')]['traps'] == 1 and rows[('org-a', 'loop')]['timeouts'] == 1
    assert rows[('org-a', 'loop')]['fuel'] == 1_000_000
    assert host.usage.drain() == [], 'drained'


def test_fuel_memory_and_the_breaker(host):
    looping = host.load(wat('(loop $l (br $l))'), {'limits': {'fuel': 1_000_000}})
    clock = [0.0]
    inst = looping.instance('x', monotonic=lambda: clock[0])
    for _ in range(BREAKER_FAILURES):
        assert inst.call({'event': 'tick'}) is None
    assert inst.resting_until == 60.0
    assert host.load(wat(), {'limits': {'fuel': 10 ** 12}}).fuel == MAX_FUEL
    grow = '(drop (memory.grow (i32.const 2000)))'
    for mib, pages in ((1, 1), (256, 2001)):
        inst = host.load(wat(grow), {'limits': {'memoryMiB': mib}}).instance('x')
        assert inst.call({'event': 'tick'}) == {}
        assert inst.exports['memory'].size(inst.store) == pages
    assert host.load(wat(answer='[1]'), {}).instance('x').call({'event': 'tick'}) is None
