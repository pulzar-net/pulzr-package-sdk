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


STATE = '''(import "pz" "state_get" (func $get (param i32 i32 i32 i32) (result i32)))
           (import "pz" "state_set" (func $set (param i32 i32 i32 i32) (result i32)))'''


def test_state_per_install_and_its_quota(host):
    data = '(data (i32.const 2048) "k") (data (i32.const 2056) "v")'
    # Read k: on a first call set it; on a later one it must be "v".
    body = '''(if (i32.eq (call $get (i32.const 2048) (i32.const 1) (i32.const 3000) (i32.const 8)) (i32.const -1))
                (then (drop (call $set (i32.const 2048) (i32.const 1) (i32.const 2056) (i32.const 1))))
                (else (if (i32.ne (i32.load8_u (i32.const 3000)) (i32.const 118)) (then unreachable))))
              ;; 2000 octets is past a 1 KiB quota: refused.
              (if (i32.ne (call $set (i32.const 2048) (i32.const 1) (i32.const 4000) (i32.const 2000)) (i32.const -1))
                (then unreachable))'''
    m = host.load(wat(body, STATE, data), {'imports': ['state'], 'limits': {'stateKiB': 1}})
    store = MemoryState()
    a, b = m.instance('org-a', state=store), m.instance('org-b', state=store)
    assert a.call({'event': 'tick'}) == {} and a.call({'event': 'tick'}) == {}
    assert store.values == {'org-a': {b'k': b'v'}}
    assert b.call({'event': 'tick'}) == {} and store.values['org-b'] == {b'k': b'v'}, 'one install each'
    # It survives the instance: a trap or a restart keeps state.
    a.call({'event': 'x'})
    again = m.instance('org-a', state=store)
    assert again.call({'event': 'tick'}) == {}


class FakeHttp:
    def __init__(self):
        self.requests = []

    def request(self, method, url, headers, body, timeout_ms=None):
        if 'api.example.com' not in url:
            raise HttpError('not a declared host')
        self.requests.append((method, url, headers, body))
        return 200, {'content-type': 'text/plain'}, b'pong'


def http_module(host, request, spec=None):
    req = json.dumps(request)
    imports = '(import "pz" "http" (func $http (param i32 i32) (result i64)))'
    esc = req.replace('\\', '\\\\').replace('"', '\\"')
    source = f'''(module {imports}
      (memory (export "memory") 1)
      (data (i32.const 1024) "{esc}")
      (global $bump (mut i32) (i32.const 32768))
      (func (export "pz_alloc") (param $n i32) (result i32) (local $p i32)
        global.get $bump local.set $p
        global.get $bump local.get $n i32.add global.set $bump local.get $p)
      (func (export "pz_abi") (result i32) i32.const 2)
      (func (export "pz_handle") (param i32 i32) (result i64)
        i32.const 1024 i32.const {len(req.encode())} call $http))'''
    spec = spec or {'imports': ['http'], 'http': {'hosts': ['api.example.com']},
                    'credentials': [{'name': 'api', 'label': 'API key',
                                     'inject': {'header': 'Authorization', 'format': 'Bearer {secret}'}}]}
    return host.load(wasmtime.wat2wasm(source), spec)


def test_http_injects_the_credential_the_module_never_sees(host):
    client = FakeHttp()
    m = http_module(host, {'method': 'POST', 'url': 'https://api.example.com/v1/ping',
                           'headers': {'Authorization': 'forged'}, 'body': 'aGk=', 'credential': 'api'})
    answer = m.instance('org-a', http=client, credentials={'api': 's3cret'}).call({'event': 'tick'})
    assert answer == {'status': 200, 'headers': {'content-type': 'text/plain'}, 'body': 'cG9uZw=='}
    method, url, headers, body = client.requests[0]
    assert (method, url, body) == ('POST', 'https://api.example.com/v1/ping', b'hi')
    assert headers == {'Authorization': 'Bearer s3cret'}, 'the module may not set the credential header'
    # Another organization's secret, or none, never this one's.
    m.instance('org-b', http=client, credentials={'api': 'other'}).call({'event': 'tick'})
    assert client.requests[1][2] == {'Authorization': 'Bearer other'}
    assert m.instance('org-c', http=client).call({'event': 'tick'}) == {'error': 'no credential api'}


def test_http_refuses_what_is_not_declared(host):
    client = FakeHttp()
    m = http_module(host, {'url': 'https://evil.example.org/'})
    assert m.instance('x', http=client).call({'event': 'tick'}) == {'error': 'not a declared host'}
    assert not client.requests


def test_the_default_client_is_https_to_declared_hosts_only():
    from pulzr_host import HttpClient
    from pulzr_host.http import allowed
    c = HttpClient(['api.example.com', '*.example.net'])
    for url in ('http://api.example.com/', 'https://evil.example.org/', 'https://user:pw@api.example.com/',
                'https://example.net/'):
        with pytest.raises(HttpError):
            c.request('GET', url, {}, b'')
    assert allowed(['*.example.net'], 'a.example.net') and not allowed(['*.example.net'], 'example.net')
    assert not allowed(['api.example.com'], 'api.example.com.evil.org')


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
