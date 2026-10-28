"""Check CI sequencing and cleanup using disposable exporter fixtures, without Docker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        tools = self.base / 'bin'
        tools.mkdir()
        self.work = self.base / 'work'
        self.work.mkdir()
        self.log = self.base / 'calls'
        docker = tools / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
group = args[-1]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if group == os.environ.get('FAIL_GROUP'):
    sys.exit(42)
results = pathlib.Path(os.environ['CI_RESULTS'])
if group in ('ci', 'container'):
    results.mkdir()
    (results / 'assembly-passed').touch()
    (results / 'live-image-amd64.hybrid.iso').write_text('synthetic')
else:
    assert group == 'exports'
    assert (pathlib.Path(os.environ['CI_ISO']) / 'live-image-amd64.hybrid.iso').is_file()
    assert os.environ['ASSEMBLY_ID_1'] != os.environ['ASSEMBLY_ID_2']
    for pair in (1, 2):
        out = results / ('fixture-' + str(pair))
        out.mkdir()
        image = out / 'usb-a.img'
        with image.open('wb') as f:
            f.truncate(1024 * 1024)
        identity = 'same' if os.environ.get('DUPLICATE') else str(pair)
        (out / 'images.json').write_text(json.dumps(dict(image_mib=1, images=dict(a=dict(
            file='usb-a.img', esp_uuid=identity+'esp', zfs_partuuid=identity+'zfs')))))
        (out / 'IMAGES-PREPARED').touch()
        export = results / ('export-' + str(pair))
        export.mkdir()
        subprocess.run(['tar', '--sparse', '-cf', str(export / 'pair.tar'), '-C', str(out), '.'], check=True)
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}',
                        TMPDIR=str(self.work), CALLS=str(self.log), BUILDX_BUILDER='level1vault-fixture')

    def run_ci(self, group='ci', **settings):
        return subprocess.run(['bash', str(ROOT / 'pipeline/ci.sh'), group],
                              env=dict(self.env, **settings), capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_both_entrypoints_validate_exports_and_remove_temporary_outputs(self):
        for group in ('ci', 'container'):
            with self.subTest(group=group):
                result = self.run_ci(group)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call[-1] for call in self.calls()][-2:], [group, 'exports'])
                self.assertEqual(list(self.work.iterdir()), [])

    def test_failed_checks_stop_before_export_and_still_clean_up(self):
        result = self.run_ci(FAIL_GROUP='ci')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_failed_export_or_reused_identity_is_not_success(self):
        for settings in ({'FAIL_GROUP': 'exports'}, {'DUPLICATE': '1'}):
            with self.subTest(settings=settings):
                result = self.run_ci(**settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(self.work.iterdir()), [])
