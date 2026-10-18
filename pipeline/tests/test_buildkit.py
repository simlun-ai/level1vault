"""Host wrapper boundaries: no real Docker daemon or filesystem mutations outside temp dirs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BuildKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        tools = self.base/'bin'
        tools.mkdir()
        self.log = self.base/'calls.jsonl'
        docker = tools/'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''import io, json, os, pathlib, sys, tarfile
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
if args[0] == 'context': print('unix:///var/run/docker.sock')
if args[0] == 'info': print(json.dumps(dict(OSType='linux', Architecture='x86_64', SecurityOptions=[])))
if args[:2] == ['buildx', 'inspect']: print('Driver: docker-container')
if args[:2] == ['buildx', 'build']:
    if os.environ.get('FAIL_EXPORT'): sys.exit(42)
    if '--build-context' in args:
        context = args[args.index('--build-context')+1].split('=',1)[1]
        expected = ['commit', 'source.bundle'] if args[args.index('--build-context')+1].startswith('repository-input=') else ['live-image-amd64.hybrid.iso']
        assert sorted(x.name for x in pathlib.Path(context).iterdir()) == expected
    dest = args[args.index('--output')+1].split('dest=',1)[1].split(',')[0]
    pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
    target = args[args.index('--target')+1]
    if target in ('artifacts', 'pair-artifacts'):
        pair = next((x.split('=', 1)[1] for x in args if x.startswith('PAIR_ID=')), '1')
        archive = pathlib.Path(dest) / (f'pairs/{pair}.tar' if target == 'artifacts' else 'pair.tar')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, 'w') as output:
            data = b'prepared image'
            image = tarfile.TarInfo('usb-a.img')
            image.size = len(data)
            output.addfile(image, io.BytesIO(data))
            output.addfile(tarfile.TarInfo('IMAGES-PREPARED'))
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', CALLS=str(self.log), TMPDIR=str(self.base))
        self.env.pop('DOCKER_HOST', None)
        self.env.pop('DOCKER_CONTEXT', None)

    def run_task(self, *args, env=None):
        return subprocess.run(['bash', str(ROOT/'pipeline/buildkit.sh'), *map(str, args)],
                              env=env or self.env, capture_output=True, text=True)

    def builds(self):
        return [x for x in map(json.loads, self.log.read_text().splitlines()) if x[:2] == ['buildx','build']]

    def test_pair_exports_use_only_iso_and_fresh_identities_without_entitlement(self):
        build = self.base/'build'
        build.mkdir()
        (build/'live').mkdir()
        (build/'live/live-image-amd64.hybrid.iso').write_text('synthetic')
        (build/'initialized-private.img').write_text('must not enter context')
        for name in ['pair-2', 'pair-3']:
            result = self.run_task('assemble', build, build/name)
            self.assertEqual(result.returncode, 0, result.stderr)
        for name in ['pair-2', 'pair-3']:
            self.assertEqual((build/name/'usb-a.img').read_bytes(), b'prepared image')
            self.assertTrue((build/name/'IMAGES-PREPARED').is_file())
            self.assertFalse((build/name/'pair.tar').exists())
        commands = self.builds()
        self.assertEqual(len(commands), 2)
        ids = [next(x for x in cmd if x.startswith('ASSEMBLY_ID=')) for cmd in commands]
        self.assertNotEqual(*ids)
        self.assertTrue(all('--allow' not in cmd for cmd in commands))
        self.assertNotEqual(self.run_task('assemble', build, build/'pair-2').returncode, 0)
        self.assertEqual(len(self.builds()), 2)

    def test_full_build_unpacks_the_selected_pair_and_removes_transport_archive(self):
        destination = self.base/'full'
        result = self.run_task('build', destination, env=dict(self.env, PAIR='7'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination/'pairs/7/usb-a.img').read_bytes(), b'prepared image')
        self.assertTrue((destination/'pairs/7/IMAGES-PREPARED').is_file())
        self.assertFalse((destination/'pairs/7.tar').exists())

    def test_failed_export_is_not_success_and_config_arguments_preserve_boundaries(self):
        env = dict(self.env, FAIL_EXPORT='1')
        result = self.run_task('build', self.base/'failed', '--apt-options', '--yes -o Example=false', env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.base/'failed/build.log').exists())
        args = self.builds()[0]
        config = next(x for x in args if x.startswith('LB_CONFIG_JSON=')).split('=',1)[1]
        self.assertEqual(json.loads(config), ['--apt-options', '--yes -o Example=false'])
        self.assertIn('security.insecure', args)
        self.assertNotEqual(self.run_task('build', self.base/'failed').returncode, 0)
        self.assertEqual(len(self.builds()), 1)

    def test_upgrade_build_exports_only_the_release_target(self):
        result = self.run_task('build-upgrade', self.base/'upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.builds()[0]
        self.assertEqual(args[args.index('--target') + 1], 'upgrade-artifacts')
        self.assertIn('security.insecure', args)
        self.assertIn('repository-input=', ' '.join(args))

    def test_existing_iso_can_export_a_release_without_sending_workspace_images(self):
        build = self.base/'build'
        (build/'live').mkdir(parents=True)
        (build/'live/live-image-amd64.hybrid.iso').write_text('synthetic')
        (build/'private-workspace.img').write_text('never enter context')
        result = self.run_task('release', build, build/'upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.builds()[0]
        self.assertEqual(args[args.index('--target') + 1], 'upgrade-from-iso-artifacts')
        self.assertNotIn('--allow', args)

    def test_initialized_images_are_rejected_before_inspection_build(self):
        build = self.base/'build'
        (build/'pairs/1').mkdir(parents=True)
        (build/'pairs/1/IMAGES-PREPARED').touch()
        (build/'pairs/1/READY').touch()
        self.assertNotEqual(self.run_task('check', build).returncode, 0)
        self.assertEqual(self.builds(), [])

    def test_clean_removes_only_recorded_checkout_builders_without_bootstrapping(self):
        tools = self.base / 'bin'
        records = self.base / 'records'
        records.mkdir()
        for builder in ('level1vault-first', 'level1vault-second'):
            (records / builder).write_text(str(ROOT) + '\n')
        git = tools / 'git'
        git.write_text('#!/bin/sh\nprintf "%s\\n" "$RECORDS/level1vault-unused"\n')
        git.chmod(0o755)
        env = dict(self.env, LEVEL1VAULT_LOCKED='1', RECORDS=str(records))
        result = self.run_task('clean', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = list(map(json.loads, self.log.read_text().splitlines()))
        self.assertEqual([cmd for cmd in calls if cmd[:2] == ['buildx', 'rm']],
                         [['buildx', 'rm', 'level1vault-first'], ['buildx', 'rm', 'level1vault-second']])
        self.assertFalse(list(records.iterdir()))
        self.assertFalse(any(cmd[:2] == ['buildx', 'create'] or '--bootstrap' in cmd for cmd in calls))
        result = self.run_task('clean', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_refuses_a_record_owned_by_another_checkout(self):
        tools = self.base / 'bin'
        records = self.base / 'records'
        records.mkdir()
        (records / 'level1vault-other').write_text('/another/checkout\n')
        git = tools / 'git'
        git.write_text('#!/bin/sh\nprintf "%s\\n" "$RECORDS/level1vault-unused"\n')
        git.chmod(0o755)
        result = self.run_task('clean', env=dict(self.env, LEVEL1VAULT_LOCKED='1', RECORDS=str(records)))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ownership differs', result.stderr)
        self.assertFalse(any(cmd[:2] == ['buildx', 'rm'] for cmd in map(json.loads, self.log.read_text().splitlines())))
