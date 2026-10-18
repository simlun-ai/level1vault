"""Select mirrors from realistic offline label topology, without importing pools."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('workspace_discover', str(ROOT / 'runtime/workspace-discover'))
spec = importlib.util.spec_from_loader(loader.name, loader)
discover = importlib.util.module_from_spec(spec)
loader.exec_module(discover)


def label(pair='1', pool='100', leaf='11', children=('11', '12')):
    return f'''------------------------------------
LABEL 0
------------------------------------
    version: 5000
    name: 'workspace-{pair}'
    state: 1
    txg: 20
    pool_guid: {pool}
    top_guid: 50
    guid: {leaf}
    vdev_children: 1
    vdev_tree:
        type: 'mirror'
        id: 0
        guid: 50
        children[0]:
            type: 'disk'
            id: 0
            guid: {children[0]}
            path: '/dev/example1'
        children[1]:
            type: 'disk'
            id: 1
            guid: {children[1]}
            path: '/dev/example2'
'''


def complete(pair='1', pool='100'):
    return [(f'{pair}-a', label(pair, pool, '11')), (f'{pair}-b', label(pair, pool, '12'))]


class DiscoveryTests(unittest.TestCase):
    def test_one_complete_mirror_and_incomplete_other_selects_complete(self):
        self.assertEqual(discover.select(complete()), '1')
        self.assertEqual(discover.select(complete('10') + [('other', label('2'))]), '10')

    def test_no_complete_or_multiple_complete_mirrors_require_explicit_id(self):
        for devices in ([], complete()[:1], complete() + complete('2', '200')):
            with self.subTest(devices=devices), self.assertRaisesRegex(ValueError, 'explicitly'):
                discover.select(devices)

    def test_copied_members_duplicate_pool_names_and_wrong_topology_are_rejected(self):
        cases = [complete()[:1] * 2, complete() + [('clone', label())],
                 complete() + [('other-pool', label(pool='200'))],
                 [('a', label()), ('b', label(leaf='13'))],
                 [('a', label()), ('b', label(leaf='12', children=('12', '13')))]]
        for devices in cases:
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                discover.select(devices)

    def test_disagreeing_labels_are_not_accepted(self):
        self.assertIsNone(discover.identity(label() + label(pool='200')))
        self.assertIsNone(discover.identity('not a ZFS label'))
        self.assertEqual(discover.identity(label() + label()), discover.identity(label()))
