"""Exercise durable recovery records independently of retained disk images."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
import pair_records as records

IDS = [str(uuid.uuid4()) for _ in range(3)]


class PairRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "build/pairs/1"
        self.out.mkdir(parents=True)
        self.durable = self.root / "records"
        (self.out / "READY").touch()
        (self.out / "WORKSPACE-INFO.txt").write_text(
            "pair=1\npool=level1-workspace-1\npool_guid=100\n"
        )
        rows = {
            m: dict(zfs_partuuid=IDS[i], esp_uuid=f"{i + 1:04X}-AAAA")
            for i, m in enumerate(("a", "b"))
        }
        (self.out / "images.json").write_text(json.dumps(dict(format=1, images=rows)))
        self.tables = [
            dict(partitions=[{}, dict(uuid=IDS[i], size=8 + i)]) for i in range(2)
        ]

    def save(self):
        with patch.object(records, "partition_table", side_effect=self.tables):
            return records.save_pair(self.out, "1", self.durable)

    def test_export_preserves_actual_sizes_and_survives_deleting_build(self):
        initial = self.save()
        import shutil

        shutil.rmtree(self.root / "build")
        current = json.loads((self.durable / "pairs/1/current.json").read_text())
        self.assertEqual(records.validate_membership(current, "1"), initial)
        self.assertEqual(current["members"]["b"]["minimum_bytes"], 9 * 512)

    def test_stale_export_cannot_undo_replacement_and_confirmation_is_explicit(self):
        before = self.save()
        after = copy.deepcopy(before)
        after["members"]["a"]["zfs_partuuid"] = IDS[2]
        directory = self.durable / "pairs/1"
        history = directory / "replacements"
        history.mkdir()
        replacement = str(uuid.uuid4())
        (history / (replacement + ".json")).write_text(
            json.dumps(dict(before=before, after=after))
        )
        with self.assertRaisesRegex(ValueError, "CONFIRMED"):
            records.accept_replacement(self.durable, "1", replacement, False)
        records.accept_replacement(self.durable, "1", replacement, True)
        records.accept_replacement(self.durable, "1", replacement, True)
        with self.assertRaisesRegex(ValueError, "membership differs"):
            self.save()
        self.assertEqual(json.loads((directory / "initial.json").read_text()), before)
        self.assertEqual(json.loads((directory / "current.json").read_text()), after)

    def test_wrong_base_and_invalid_records_do_not_change_current(self):
        before = self.save()
        directory = self.durable / "pairs/1"
        history = directory / "replacements"
        history.mkdir()
        replacement = str(uuid.uuid4())
        other = copy.deepcopy(before)
        other["pool_guid"] = "200"
        (history / (replacement + ".json")).write_text(
            json.dumps(dict(before=other, after=other))
        )
        with self.assertRaisesRegex(ValueError, "membership changed"):
            records.accept_replacement(self.durable, "1", replacement, True)
        self.assertEqual(json.loads((directory / "current.json").read_text()), before)
        with self.assertRaises(ValueError):
            records.validate_membership(before, "2")
        with self.assertRaises(ValueError):
            records.require_separate(self.root / "build/records", self.root / "build")
