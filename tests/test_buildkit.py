"""Host wrapper boundaries: no real Docker daemon or filesystem mutations outside temp dirs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        tools = self.base/'bin'
        tools.mkdir()
        self.log = self.base/'calls.jsonl'
        docker = tools/'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
if args[0] == 'context': print('unix:///var/run/docker.sock')
if args[0] == 'info': print(json.dumps(dict(OSType='linux', Architecture='x86_64', SecurityOptions=[])))
if args[:2] == ['buildx', 'inspect'] and '--format' in args: print('docker-container')
if args[:2] == ['buildx', 'build']:
    if os.environ.get('FAIL_EXPORT'): sys.exit(42)
    if '--build-context' in args:
        context = args[args.index('--build-context')+1].split('=',1)[1]
        assert sorted(x.name for x in pathlib.Path(context).iterdir()) == ['live-image-amd64.hybrid.iso']
    dest = args[args.index('--output')+1].split('dest=',1)[1].split(',')[0]
    pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', CALLS=str(self.log), TMPDIR=str(self.base))
        self.env.pop('DOCKER_HOST', None)
        self.env.pop('DOCKER_CONTEXT', None)

    def run_task(self, *args, env=None):
        return subprocess.run(['bash', str(ROOT/'scripts/buildkit.sh'), *map(str, args)],
                              env=env or self.env, capture_output=True, text=True)

    def builds(self):
        return [x for x in map(json.loads, self.log.read_text().splitlines()) if x[:2] == ['buildx','build']]

    def test_pair_exports_use_only_iso_and_fresh_identities_without_entitlement(self):
        build = self.base/'build'
        build.mkdir()
        (build/'live-image-amd64.hybrid.iso').write_text('synthetic')
        (build/'initialized-private.img').write_text('must not enter context')
        for name in ['pair-2', 'pair-3']:
            result = self.run_task('assemble', build, build/name)
            self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.builds()
        self.assertEqual(len(commands), 2)
        ids = [next(x for x in cmd if x.startswith('ASSEMBLY_ID=')) for cmd in commands]
        self.assertNotEqual(*ids)
        self.assertTrue(all('--allow' not in cmd for cmd in commands))
        self.assertNotEqual(self.run_task('assemble', build, build/'pair-2').returncode, 0)
        self.assertEqual(len(self.builds()), 2)

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

    def test_initialized_images_are_rejected_before_inspection_build(self):
        build = self.base/'build'
        (build/'out').mkdir(parents=True)
        (build/'out/IMAGES-PREPARED').touch()
        (build/'out/READY').touch()
        self.assertNotEqual(self.run_task('check', build).returncode, 0)
        self.assertEqual(self.builds(), [])
