"""Exercise the real sparse transport and incomplete-extraction boundary."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PairArchiveTests(unittest.TestCase):
    def run_archive(self, action, source, destination):
        return subprocess.run(['bash', str(ROOT/'pipeline/pair-archive.sh'), action,
                               str(source), str(destination)], capture_output=True, text=True)

    def test_sparse_images_survive_transport_with_content_size_and_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base/'source'
            source.mkdir()
            size = 64 * 1024 * 1024
            for name in ('usb-a.img', 'usb-b.img'):
                with (source/name).open('wb') as image:
                    image.write(name.encode())
                    image.seek(size//2)
                    image.write(b'middle of disk')
                    image.seek(size-16)
                    image.write(b'backup GPT here!')
                    image.truncate(size)
            (source/'IMAGES-PREPARED').touch()
            (source/'images.json').write_text('{"images": {}}\n')
            archive = base/'pair.tar'
            result = self.run_archive('pack', source, archive)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(archive.stat().st_size, size//8)
            destination = base/'new pair'
            result = self.run_archive('unpack', archive, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            for original in source.iterdir():
                extracted = destination/original.name
                self.assertEqual(extracted.read_bytes(), original.read_bytes())
                self.assertEqual(extracted.stat().st_uid, os.getuid())
                if extracted.suffix == '.img':
                    self.assertEqual(extracted.stat().st_size, size)
                    self.assertLess(extracted.stat().st_blocks*512, size//8)
            result = self.run_archive('unpack', archive, destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((destination/'images.json').read_bytes(), (source/'images.json').read_bytes())

    def test_failed_or_incomplete_archive_never_publishes_a_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base/'source'
            source.mkdir()
            (source/'usb-a.img').write_bytes(b'image data')
            archive = base/'pair.tar'
            self.assertEqual(self.run_archive('pack', source, archive).returncode, 0)
            destination = base/'pair'
            self.assertNotEqual(self.run_archive('unpack', archive, destination).returncode, 0)
            self.assertFalse(destination.exists())
            archive.write_bytes(b'truncated archive')
            self.assertNotEqual(self.run_archive('unpack', archive, destination).returncode, 0)
            self.assertFalse(destination.exists())
            self.assertFalse(list(base.glob('.pair-unpack.*')))
