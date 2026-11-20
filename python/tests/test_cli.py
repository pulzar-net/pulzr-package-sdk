"""pulzr-pkg's checks, as the platform makes them."""

import json
import pathlib

from pulzr_pkg import cli

EXAMPLE = json.loads((pathlib.Path(__file__).parents[2] / 'examples' / 'beacon-gateway' / 'gateway.json').read_text())
A = 'exbg-aabbccfffeddeeff'


def test_an_answer_is_checked_as_the_platform_checks_it():
    good = {'protocol': [{'adapter': A, 'key': 'gw:aabbccfffeddeeff', 'kind': 'cmd/res',
                          'body': {'v': 1, 'id': '10000042', 'status': 'sent'}}],
            'publish': [{'topic': 'example/down/AABBCCDDEEFF', 'payload': 'aGk='}]}
    assert cli.check_gateway_answer(EXAMPLE, good) == []
    bad = {'protocol': [{'adapter': 'other-1', 'key': 'nope', 'kind': 'up', 'body': {'v': 1}},
                        {'adapter': A, 'key': 'gw:x', 'kind': 'shout', 'body': {}}],
           'publish': [{'topic': 'elsewhere/x', 'payload': ''}]}
    problems = cli.check_gateway_answer(EXAMPLE, bad)
    assert any('not as exbg-' in p for p in problems)
    assert any('not a device key' in p for p in problems)
    assert any("'shout'" in p for p in problems)
    assert any('may not publish on elsewhere/x' in p for p in problems)
    assert any(p.startswith('nope up:') for p in problems), 'the body against the schema'


def test_expect_contains():
    assert cli._matches({'a': [{'b': 1}]}, {'a': [{'b': 1, 'c': 2}], 'd': 3})
    assert not cli._matches({'a': [{'b': 1}]}, {'a': [{'b': 1}, {'b': 2}]})
    assert cli._topic_matches('a/+/c', 'a/b/c') and not cli._topic_matches('a/+', 'a/b/c')
