#!/usr/bin/env python3
"""
validate_bridge_index.py – offline unit-test / smoke-test for the BRIDGE_IDX
routing index added to bridge_master.py to fix audio dropout under high load.

Run directly (no live RYSEN instance required):

    python3 tools/validate_bridge_index.py

All tests must pass with exit code 0.  If any assertion fails the script
prints which test failed and exits with code 1.

What is tested
--------------
1. rebuild_bridge_index() produces correct (system, ts, tgid)->bridge_name map.
2. _idx_add_bridge() incrementally adds a new bridge without touching others.
3. _idx_remove_bridge() removes all references to a bridge without leaving
   stale entries.
4. _idx_replace_bridge() correctly refreshes entries when a bridge's content
   changes.
5. Hot-path simulation: indexed lookup returns exactly the same bridges as the
   equivalent O(N*M) full scan (correctness parity).
6. Removing a bridge that does not exist does not raise an exception.
7. Index is consistent after a sequence of add / remove / replace operations.
"""
import sys
import traceback
from time import time

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported standalone without all of
# bridge_master.py's heavy dependencies.
# ---------------------------------------------------------------------------

# Simulate the module-level globals that the index helpers depend on
BRIDGES = {}
BRIDGE_IDX = {}

# ---- replicated helpers (copy of the live code) ---------------------------

def _idx_add_bridge(bridge_name):
    for e in BRIDGES.get(bridge_name, ()):
        _key = (e['SYSTEM'], e['TS'], e['TGID'])
        if _key not in BRIDGE_IDX:
            BRIDGE_IDX[_key] = set()
        BRIDGE_IDX[_key].add(bridge_name)


def _idx_remove_bridge(bridge_name):
    empty_keys = [k for k, v in BRIDGE_IDX.items() if bridge_name in v]
    for _key in empty_keys:
        BRIDGE_IDX[_key].discard(bridge_name)
        if not BRIDGE_IDX[_key]:
            del BRIDGE_IDX[_key]


def _idx_replace_bridge(bridge_name):
    _idx_remove_bridge(bridge_name)
    _idx_add_bridge(bridge_name)


def rebuild_bridge_index():
    global BRIDGE_IDX
    new_idx = {}
    for _bname, _entries in BRIDGES.items():
        for e in _entries:
            _key = (e['SYSTEM'], e['TS'], e['TGID'])
            if _key not in new_idx:
                new_idx[_key] = set()
            new_idx[_key].add(_bname)
    BRIDGE_IDX = new_idx


# ---- helpers ---------------------------------------------------------------

def _entry(system, ts, tgid, active=True):
    return {'SYSTEM': system, 'TS': ts, 'TGID': tgid, 'ACTIVE': active,
            'TIMEOUT': 300, 'TO_TYPE': 'ON', 'OFF': [], 'ON': [], 'RESET': [],
            'TIMER': time()}


def _full_scan_lookup(source_system, slot, dst_id):
    """Reference O(N*M) lookup – same logic as the pre-optimisation code."""
    results = []
    for _bridge in BRIDGES:
        for _entry_item in BRIDGES[_bridge]:
            if (_entry_item['SYSTEM'] == source_system
                    and _entry_item['TGID'] == dst_id
                    and _entry_item['TS'] == slot
                    and _entry_item['ACTIVE'] is True):
                results.append(_bridge)
    return sorted(set(results))


def _indexed_lookup(source_system, slot, dst_id):
    """Indexed O(1) lookup – same logic as the optimised code."""
    _key = (source_system, slot, dst_id)
    bridges = BRIDGE_IDX.get(_key)
    if bridges is None:
        return []
    # Only return bridges where at least one entry truly matches (ACTIVE check)
    matched = []
    for _bridge in bridges:
        if _bridge not in BRIDGES:
            continue
        for e in BRIDGES[_bridge]:
            if (e['SYSTEM'] == source_system
                    and e['TGID'] == dst_id
                    and e['TS'] == slot
                    and e['ACTIVE'] is True):
                matched.append(_bridge)
                break
    return sorted(set(matched))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
_failures = []


def _test(name, fn):
    global BRIDGES, BRIDGE_IDX
    BRIDGES = {}
    BRIDGE_IDX = {}
    try:
        fn()
        print(f'  PASS  {name}')
    except AssertionError as exc:
        _failures.append(name)
        print(f'  FAIL  {name}: {exc}')
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_rebuild_basic():
    BRIDGES['100'] = [
        _entry('SYS-A', 1, b'\x00\x00\x64'),
        _entry('SYS-B', 2, b'\x00\x00\x64'),
    ]
    BRIDGES['#100'] = [
        _entry('SYS-A', 2, b'\x00\x00\x09'),
    ]
    rebuild_bridge_index()

    assert ('SYS-A', 1, b'\x00\x00\x64') in BRIDGE_IDX
    assert '100' in BRIDGE_IDX[('SYS-A', 1, b'\x00\x00\x64')]

    assert ('SYS-B', 2, b'\x00\x00\x64') in BRIDGE_IDX
    assert '100' in BRIDGE_IDX[('SYS-B', 2, b'\x00\x00\x64')]

    assert ('SYS-A', 2, b'\x00\x00\x09') in BRIDGE_IDX
    assert '#100' in BRIDGE_IDX[('SYS-A', 2, b'\x00\x00\x09')]


def test_idx_add_bridge():
    BRIDGES['200'] = [_entry('SYS-C', 1, b'\x00\x00\xc8')]
    rebuild_bridge_index()

    BRIDGES['201'] = [_entry('SYS-C', 1, b'\x00\x00\xc9')]
    _idx_add_bridge('201')

    key_200 = ('SYS-C', 1, b'\x00\x00\xc8')
    key_201 = ('SYS-C', 1, b'\x00\x00\xc9')
    assert key_200 in BRIDGE_IDX and '200' in BRIDGE_IDX[key_200]
    assert key_201 in BRIDGE_IDX and '201' in BRIDGE_IDX[key_201]


def test_idx_remove_bridge():
    BRIDGES['300'] = [_entry('SYS-D', 1, b'\x00\x01\x2c')]
    BRIDGES['301'] = [_entry('SYS-D', 1, b'\x00\x01\x2d')]
    rebuild_bridge_index()

    _idx_remove_bridge('300')
    del BRIDGES['300']

    key = ('SYS-D', 1, b'\x00\x01\x2c')
    assert key not in BRIDGE_IDX, 'Removed bridge key still in index'

    key2 = ('SYS-D', 1, b'\x00\x01\x2d')
    assert key2 in BRIDGE_IDX and '301' in BRIDGE_IDX[key2]


def test_idx_replace_bridge():
    TG_OLD = b'\x00\x01\x90'
    TG_NEW = b'\x00\x01\x91'
    BRIDGES['400'] = [_entry('SYS-E', 2, TG_OLD)]
    rebuild_bridge_index()

    # Simulate changing the TGID in the bridge
    BRIDGES['400'] = [_entry('SYS-E', 2, TG_NEW)]
    _idx_replace_bridge('400')

    old_key = ('SYS-E', 2, TG_OLD)
    new_key = ('SYS-E', 2, TG_NEW)
    assert old_key not in BRIDGE_IDX, 'Old key still present after replace'
    assert new_key in BRIDGE_IDX and '400' in BRIDGE_IDX[new_key]


def test_lookup_parity():
    """Indexed and full-scan lookups must agree for every key."""
    TG1 = b'\x00\x13\x88'   # TG 5000
    TG2 = b'\x00\x13\x89'   # TG 5001

    BRIDGES['5000'] = [
        _entry('MASTER-0', 1, TG1, active=True),
        _entry('MASTER-0', 2, TG1, active=False),
        _entry('MASTER-1', 1, TG1, active=True),
    ]
    BRIDGES['5001'] = [
        _entry('MASTER-0', 1, TG2, active=True),
    ]
    rebuild_bridge_index()

    test_cases = [
        ('MASTER-0', 1, TG1),
        ('MASTER-0', 2, TG1),
        ('MASTER-1', 1, TG1),
        ('MASTER-0', 1, TG2),
        ('MASTER-2', 1, TG1),   # system not in any bridge
    ]
    for system, slot, dst_id in test_cases:
        full = _full_scan_lookup(system, slot, dst_id)
        indexed = _indexed_lookup(system, slot, dst_id)
        assert full == indexed, (
            f'Lookup mismatch for ({system}, {slot}, {dst_id}): '
            f'full={full} indexed={indexed}'
        )


def test_remove_nonexistent_bridge():
    """Removing a bridge that was never added must not raise."""
    BRIDGES['600'] = [_entry('SYS-F', 1, b'\x00\x02\x58')]
    rebuild_bridge_index()
    # Remove a bridge that was never added to BRIDGES or the index
    _idx_remove_bridge('999')   # should not raise


def test_sequence_consistency():
    """Run a sequence of add/remove/replace and verify index stays consistent."""
    TG_A = b'\x00\x0a\x00'
    TG_B = b'\x00\x0b\x00'

    BRIDGES['700'] = [_entry('SYS-G', 1, TG_A)]
    BRIDGES['701'] = [_entry('SYS-G', 1, TG_B)]
    rebuild_bridge_index()

    # Add a new bridge
    BRIDGES['702'] = [_entry('SYS-G', 2, TG_A)]
    _idx_add_bridge('702')

    # Replace an existing bridge's entries
    BRIDGES['700'] = [_entry('SYS-G', 1, TG_A), _entry('SYS-H', 2, TG_A)]
    _idx_replace_bridge('700')

    # Remove one bridge
    _idx_remove_bridge('701')
    del BRIDGES['701']

    # Now verify full parity
    for system in ('SYS-G', 'SYS-H', 'SYS-Z'):
        for slot in (1, 2):
            for tg in (TG_A, TG_B):
                full = _full_scan_lookup(system, slot, tg)
                indexed = _indexed_lookup(system, slot, tg)
                assert full == indexed, (
                    f'Sequence consistency fail for ({system}, {slot}, {tg}): '
                    f'full={full} indexed={indexed}'
                )


def test_many_systems_lookup():
    """
    Simulate GENERATOR=200: 200 generated systems all sharing a TG.
    The index should still give the correct single bridge name instantly.
    """
    TG = b'\x00\x00\x64'
    entries = []
    for i in range(200):
        entries.append(_entry(f'MASTER-{i}', 1, TG, active=(i == 0)))
        entries.append(_entry(f'MASTER-{i}', 2, TG, active=False))
    BRIDGES['100'] = entries
    rebuild_bridge_index()

    # Only MASTER-0 / slot 1 is active
    full = _full_scan_lookup('MASTER-0', 1, TG)
    indexed = _indexed_lookup('MASTER-0', 1, TG)
    assert full == indexed == ['100'], (
        f'GENERATOR parity fail: full={full} indexed={indexed}')

    # Inactive entry should not appear in either path
    full2 = _full_scan_lookup('MASTER-1', 1, TG)
    indexed2 = _indexed_lookup('MASTER-1', 1, TG)
    assert full2 == indexed2 == [], (
        f'Inactive system unexpectedly returned: full={full2} indexed={indexed2}')


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('Running BRIDGE_IDX validation tests...\n')

    tests = [
        ('rebuild_basic',           test_rebuild_basic),
        ('idx_add_bridge',          test_idx_add_bridge),
        ('idx_remove_bridge',       test_idx_remove_bridge),
        ('idx_replace_bridge',      test_idx_replace_bridge),
        ('lookup_parity',           test_lookup_parity),
        ('remove_nonexistent',      test_remove_nonexistent_bridge),
        ('sequence_consistency',    test_sequence_consistency),
        ('many_systems_lookup',     test_many_systems_lookup),
    ]

    for name, fn in tests:
        _test(name, fn)

    print()
    if _failures:
        print(f'RESULT: {len(_failures)}/{len(tests)} tests FAILED: {_failures}')
        sys.exit(1)
    else:
        print(f'RESULT: All {len(tests)} tests PASSED.')
        sys.exit(0)
