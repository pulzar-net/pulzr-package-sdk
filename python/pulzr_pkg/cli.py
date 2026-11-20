"""pulzr-pkg: make, build, check and test a Pulzr extension module.

    pulzr-pkg new gateway my-gateway     a gateway support package to start from
    pulzr-pkg new carrier my-carrier     a carrier driver to start from
    pulzr-pkg build                      cargo build for wasm32, the module into dist/, its sha256 into the manifest
    pulzr-pkg validate                   the manifest, the module's digest, imports, exports and limits
    pulzr-pkg test                       tests/*.jsonl through the platform's own host
    pulzr-pkg assets                     the files a release must carry, one per line

Run in the package's directory. The manifest is `gateway.json` for a gateway
package, `driver.json` for a carrier driver (`--manifest` for another).
"""

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

import jsonschema

import pulzr_host

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA = HERE / 'schema'
TEMPLATES = HERE / 'templates'
KINDS = {'gateway.json': ('gateway', 'adapter'), 'driver.json': ('carrier', 'driver')}
PROTOCOL_KINDS = {'up': 'up', 'state': 'state', 'settings': 'settings', 'event': 'event', 'cmd/res': 'cmdResult'}


class Failed(Exception):
    pass


def _load(name):
    return json.loads((SCHEMA / name).read_text())


def manifest_path(args):
    if args.manifest:
        return pathlib.Path(args.manifest)
    for name in KINDS:
        if pathlib.Path(name).exists():
            return pathlib.Path(name)
    raise Failed('no gateway.json or driver.json here (--manifest names another)')


def kind_of(path, manifest):
    kind, key = KINDS.get(path.name, ('other', None))
    if key is None:
        key = next((k for k, v in manifest.items() if isinstance(v, dict) and v.get('runtime') == 'wasm'), None)
    if key is None or not isinstance(manifest.get(key), dict):
        raise Failed(f'{path}: no module object (adapter, driver, or one with "runtime": "wasm")')
    return kind, key


# -- new -------------------------------------------------------------------------------

def cmd_new(args):
    source = TEMPLATES / args.kind
    target = pathlib.Path(args.name)
    if target.exists():
        raise Failed(f'{target} already exists')
    crate = target.name.replace('_', '-')
    shutil.copytree(source, target)
    for f in target.rglob('*'):
        if f.is_file():
            text = f.read_text()
            f.write_text(text.replace('{{name}}', crate).replace('{{crate}}', crate.replace('-', '_')))
    print(f'{target}: a {args.kind} {"package" if args.kind == "gateway" else "driver"}. '
          f'Next: cd {target} && pulzr-pkg build && pulzr-pkg test')


# -- build -----------------------------------------------------------------------------

def cmd_build(args):
    path = manifest_path(args)
    manifest = json.loads(path.read_text())
    _kind, key = kind_of(path, manifest)
    run = ['cargo', 'build', '--release', '--target', 'wasm32-unknown-unknown']
    print('$', ' '.join(run))
    subprocess.run(run, check=True)
    meta = json.loads(subprocess.run(['cargo', 'metadata', '--format-version', '1', '--no-deps'],
                                     check=True, capture_output=True, text=True).stdout)
    here = pathlib.Path.cwd().resolve()
    package = next((p for p in meta['packages'] if pathlib.Path(p['manifest_path']).parent.resolve() == here), None)
    if package is None:
        raise Failed('no Cargo package in this directory')
    built = pathlib.Path(meta['target_directory']) / 'wasm32-unknown-unknown' / 'release' / \
        (package['name'].replace('-', '_') + '.wasm')
    module = manifest[key]
    dist = pathlib.Path('dist')
    dist.mkdir(exist_ok=True)
    shutil.copyfile(built, dist / module['module'])
    digest = hashlib.sha256((dist / module['module']).read_bytes()).hexdigest()
    if module.get('sha256') != digest:
        module['sha256'] = digest
        path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'dist/{module["module"]}: {(dist / module["module"]).stat().st_size} octets, sha256 {digest}')


# -- validate ----------------------------------------------------------------------------

def validate(path):
    """The loaded module and the manifest, or raises Failed with every reason."""
    manifest = json.loads(path.read_text())
    kind, key = kind_of(path, manifest)
    problems = []
    if kind == 'gateway':
        schema = _load('gateway.schema.json')
        v = jsonschema.Draft202012Validator({**schema, '$ref': '#/$defs/manifest'})
    else:
        schema = _load('extension.schema.json')
        v = jsonschema.Draft202012Validator({**schema, '$ref': '#/$defs/module'})
    target = manifest if kind == 'gateway' else manifest[key]
    problems += [f'{path}: {"/".join(map(str, e.absolute_path)) or "(top)"}: {e.message}'
                 for e in v.iter_errors(target)]
    module = manifest[key]
    if kind == 'gateway' and 'http' in (module.get('imports') or ()):
        problems.append(f'{path}: a gateway package may not import http (EXTENSIONS.md)')
    if kind == 'gateway':
        for f in ('settings', 'state', 'commands'):
            name = (manifest.get('management') or {}).get(f)
            if name and not pathlib.Path(name).exists():
                problems.append(f'{path}: management.{f} names {name}, which is not here')
    wasm_path = pathlib.Path('dist') / module.get('module', '')
    loaded = None
    if not wasm_path.is_file():
        problems.append(f'{wasm_path}: missing (pulzr-pkg build)')
    else:
        wasm = wasm_path.read_bytes()
        if hashlib.sha256(wasm).hexdigest() != module.get('sha256'):
            problems.append(f'{wasm_path} does not match {key}.sha256 (pulzr-pkg build writes it)')
        try:
            loaded = pulzr_host.Host().load(wasm, module, name=manifest.get('id', path.stem))
        except pulzr_host.LoadError as e:
            problems.append(str(e))
    fuel = (module.get('limits') or {}).get('fuel')
    if fuel and fuel > pulzr_host.MAX_FUEL:
        problems.append(f'{key}.limits.fuel {fuel} is past the platform\'s {pulzr_host.MAX_FUEL}; '
                        'calls get the platform\'s')
    if problems:
        raise Failed('\n'.join(problems))
    return loaded, manifest, kind, key


def cmd_validate(args):
    _loaded, manifest, kind, _key = validate(manifest_path(args))
    print(f'{manifest.get("id", "module")} {manifest.get("version", "")}: a valid {kind} module')


# -- test --------------------------------------------------------------------------------

def _matches(want, got):
    """`got` holds everything `want` says (lists in order, objects by key)."""
    if isinstance(want, dict):
        return isinstance(got, dict) and all(k in got and _matches(v, got[k]) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(got, list) and len(want) == len(got) and all(_matches(a, b) for a, b in zip(want, got))
    return want == got


def check_gateway_answer(manifest, answer):
    """What the platform would refuse in a gateway package's answer."""
    out = []
    protocol = _load('protocol.schema.json')
    prefix = manifest['adapter']['prefix']
    for m in answer.get('protocol') or []:
        definition = PROTOCOL_KINDS.get(m.get('kind'))
        if definition is None:
            out.append(f'protocol kind {m.get("kind")!r} is not one')
            continue
        if not str(m.get('adapter', '')).startswith(f'{prefix}-'):
            out.append(f'speaks as {m.get("adapter")}, not as {prefix}-')
        v = jsonschema.Draft202012Validator({**protocol, '$ref': f'#/$defs/{definition}'})
        out += [f'{m.get("key")} {m.get("kind")}: {e.message}' for e in v.iter_errors(m.get('body'))]
        key = jsonschema.Draft202012Validator({**protocol, '$ref': '#/$defs/deviceKey'})
        if not key.is_valid(m.get('key')):
            out.append(f'{m.get("key")!r} is not a device key')
    grants = [g['topic'] for g in (manifest.get('broker') or {}).get('adapter') or [] if g['allow'] == 'publish']
    for p in answer.get('publish') or []:
        if not any(_topic_matches(f, p.get('topic', '')) for f in grants):
            out.append(f'may not publish on {p.get("topic")}')
    return out


def _topic_matches(flt, topic):
    f, t = flt.split('/'), topic.split('/')
    for i, level in enumerate(f):
        if level == '#':
            return True
        if i >= len(t) or level not in ('+', t[i]):
            return False
    return len(f) == len(t)


def cmd_test(args):
    loaded, manifest, kind, _key = validate(manifest_path(args))
    files = sorted(pathlib.Path('tests').glob('*.jsonl'))
    if not files:
        raise Failed('no tests/*.jsonl')
    state = pulzr_host.MemoryState()
    failures = total = 0
    for f in files:
        instances = {}
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip() or line.lstrip().startswith('//'):
                continue
            case = json.loads(line)
            install = case.get('install', 'test')
            inst = instances.get(install)
            if inst is None:
                inst = instances[install] = loaded.instance(install, state=state,
                                                            credentials=case.get('credentials') or {},
                                                            clock=lambda case=case: case.get('now', 0.0))
            total += 1
            answer = inst.call(case['event'])
            problems = []
            if answer is None:
                problems.append('the module failed (see the log above)')
            else:
                if kind == 'gateway':
                    problems += check_gateway_answer(manifest, answer)
                if 'expect' in case and answer != case['expect']:
                    problems.append(f'expected {json.dumps(case["expect"])}\n           got {json.dumps(answer)}')
                if 'expect_contains' in case and not _matches(case['expect_contains'], answer):
                    problems.append(f'expected at least {json.dumps(case["expect_contains"])}\n'
                                    f'           got {json.dumps(answer)}')
            name = case.get('name', f'{f.name}:{n}')
            if problems:
                failures += 1
                print(f'FAIL {name}\n  ' + '\n  '.join(problems))
            elif args.verbose:
                print(f'ok   {name}')
    print(f'{total - failures} of {total} passed')
    if failures:
        raise Failed(f'{failures} failed')


# -- assets ------------------------------------------------------------------------------

def cmd_assets(args):
    path = manifest_path(args)
    manifest = json.loads(path.read_text())
    _kind, key = kind_of(path, manifest)
    files = [str(path)]
    for f in ('settings', 'state', 'commands'):
        name = (manifest.get('management') or {}).get(f)
        if name:
            files.append(name)
    files.append(str(pathlib.Path('dist') / manifest[key]['module']))
    print('\n'.join(files))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pulzr-pkg', description=__doc__.split('\n\n')[0])
    parser.add_argument('--manifest', help='the manifest, if not gateway.json or driver.json')
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new', help='start a package from a template')
    new.add_argument('kind', choices=['gateway', 'carrier'])
    new.add_argument('name')
    sub.add_parser('build', help='build the module for wasm32 into dist/ and record its sha256')
    sub.add_parser('validate', help='check the manifest and the module')
    test = sub.add_parser('test', help='run tests/*.jsonl through the platform host')
    test.add_argument('-v', '--verbose', action='store_true')
    sub.add_parser('assets', help='the files a release carries')
    args = parser.parse_args(argv)
    try:
        {'new': cmd_new, 'build': cmd_build, 'validate': cmd_validate, 'test': cmd_test,
         'assets': cmd_assets}[args.command](args)
    except Failed as e:
        print(f'pulzr-pkg: {e}', file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f'pulzr-pkg: {" ".join(e.cmd)} failed', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
