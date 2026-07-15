#!/usr/bin/env python3
"""Dial-a-tg TS2·TG 9 isolation — no stray routing, #9 never a reflector."""
import copy
import unittest

from dmr_utils3.utils import bytes_3

from bridge_helpers import (
    DIAL_A_TG,
    build_bridge_index,
    clear_default_reflectors_for_system,
    collect_group_route_bridges,
    is_dial_service_code,
    is_invalid_dial_reflector,
    private_call_may_create_reflector,
    to_target_forward_systems,
)


def _reflector_bridge(reflector, source_system, source_active=True, peers=None, obp=None):
    """Minimal #NNNN dial-a-tg bridge (TS2·TG 9 source leg + cold peer legs)."""
    tgid_b = bytes_3(DIAL_A_TG)
    linked_b = bytes_3(reflector)
    bridge_name = f'#{reflector}'
    entries = [{
        'SYSTEM': source_system,
        'TS': 2,
        'TGID': tgid_b,
        'ACTIVE': source_active,
        'TO_TYPE': 'ON',
        'ON': [linked_b],
    }]
    for peer in peers or ('SYSTEM-6', 'SYSTEM-7'):
        entries.append({
            'SYSTEM': peer,
            'TS': 2,
            'TGID': tgid_b,
            'ACTIVE': False,
            'TO_TYPE': 'ON',
            'ON': [linked_b],
        })
    if obp:
        entries.append({
            'SYSTEM': obp,
            'TS': 1,
            'TGID': linked_b,
            'ACTIVE': True,
            'TO_TYPE': 'NONE',
            'ON': [],
        })
    return {bridge_name: entries}


def sanitize_dial_reflectors_for_system(bridges, system):
    """Test helper: mirror sanitize_dial_reflectors() against a bridges dict."""
    changed = False
    dial_tg = bytes_3(DIAL_A_TG)
    for bridge_name in bridges:
        if bridge_name[0:1] != '#':
            continue
        for entry in bridges[bridge_name]:
            if entry['SYSTEM'] != system:
                continue
            if is_dial_service_code(bridge_name[1:]):
                if entry.get('ACTIVE'):
                    entry['ACTIVE'] = False
                    changed = True
                if entry.get('ON'):
                    entry['ON'] = []
                    changed = True
            elif dial_tg in entry.get('ON', []):
                entry['ON'] = [x for x in entry['ON'] if x != dial_tg]
                changed = True
    return changed


class TestDialTg9Guards(unittest.TestCase):

    def test_tg9_is_invalid_reflector(self):
        self.assertTrue(is_invalid_dial_reflector(9))
        self.assertTrue(is_invalid_dial_reflector('9'))
        self.assertFalse(is_invalid_dial_reflector(2350))

    def test_tg9_is_dial_service_code(self):
        self.assertTrue(is_dial_service_code(9))
        self.assertTrue(is_dial_service_code(4000))
        self.assertTrue(is_dial_service_code(5000))
        self.assertFalse(is_dial_service_code(2350))

    def test_make_single_reflector_guard_blocks_nine(self):
        """make_single_reflector() bails out when is_invalid_dial_reflector (TG 9)."""
        self.assertTrue(is_invalid_dial_reflector(DIAL_A_TG))
        self.assertFalse(is_invalid_dial_reflector(2350))


class TestPrivateCallReflectorNine(unittest.TestCase):

    def test_private_to_nine_never_creates_reflector(self):
        self.assertFalse(private_call_may_create_reflector(9, {}))
        self.assertFalse(private_call_may_create_reflector(9, {'#9': []}))

    def test_private_to_reflector_still_allowed(self):
        self.assertTrue(private_call_may_create_reflector(2350, {}))


class TestSanitizeReflectorNine(unittest.TestCase):

    def test_sanitize_clears_poisoned_hash_nine(self):
        bridges = {
            '#9': [{
                'SYSTEM': 'SYSTEM-5',
                'TS': 2,
                'TGID': bytes_3(9),
                'ACTIVE': True,
                'ON': [bytes_3(9)],
            }],
        }
        self.assertTrue(sanitize_dial_reflectors_for_system(bridges, 'SYSTEM-5'))
        entry = bridges['#9'][0]
        self.assertFalse(entry['ACTIVE'])
        self.assertEqual(entry['ON'], [])

    def test_sanitize_strips_dial_tg_from_on_list(self):
        bridges = {
            '#2350': [{
                'SYSTEM': 'SYSTEM-5',
                'TS': 2,
                'TGID': bytes_3(9),
                'ACTIVE': True,
                'ON': [bytes_3(9), bytes_3(2350)],
            }],
        }
        sanitize_dial_reflectors_for_system(bridges, 'SYSTEM-5')
        self.assertEqual(bridges['#2350'][0]['ON'], [bytes_3(2350)])


class TestStaleDefaultReflector(unittest.TestCase):
    """Stale #NNNN default reflector (TO_TYPE OFF) on proxy slot reuse."""

    def _default_reflector_bridge(self, reflector, system, active=True):
        return {
            f'#{reflector}': [{
                'SYSTEM': system,
                'TS': 2,
                'TGID': bytes_3(DIAL_A_TG),
                'ACTIVE': active,
                'TO_TYPE': 'OFF',
                'ON': [bytes_3(reflector)],
            }],
        }

    def test_clear_default_reflectors_deactivates_stale_off_legs(self):
        bridges = self._default_reflector_bridge(23426, 'SYSTEM-112', active=True)
        self.assertTrue(clear_default_reflectors_for_system(bridges, 'SYSTEM-112'))
        self.assertFalse(bridges['#23426'][0]['ACTIVE'])

    def test_clear_default_reflectors_leaves_user_activated_on(self):
        bridges = _reflector_bridge(23426, 'SYSTEM-112', source_active=True)
        self.assertFalse(clear_default_reflectors_for_system(bridges, 'SYSTEM-112'))
        self.assertTrue(bridges['#23426'][0]['ACTIVE'])

    def test_clear_default_reflectors_does_not_touch_other_systems(self):
        bridges = self._default_reflector_bridge(23426, 'SYSTEM-15', active=True)
        bridges['#23426'].append({
            'SYSTEM': 'SYSTEM-99',
            'TS': 2,
            'TGID': bytes_3(DIAL_A_TG),
            'ACTIVE': True,
            'TO_TYPE': 'OFF',
            'ON': [bytes_3(23426)],
        })
        clear_default_reflectors_for_system(bridges, 'SYSTEM-15')
        self.assertFalse(bridges['#23426'][0]['ACTIVE'])
        self.assertTrue(bridges['#23426'][1]['ACTIVE'])

    def test_slot_handoff_no_active_default_after_clear(self):
        """Prior peer left #23426 TO_TYPE OFF active; new peer slot cleanup clears it."""
        bridges = self._default_reflector_bridge(23426, 'SYSTEM-127', active=True)
        clear_default_reflectors_for_system(bridges, 'SYSTEM-127')
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-127', 2, bytes_3(DIAL_A_TG))
        self.assertEqual(routed, [])


class TestGroupTg9RoutingIsolation(unittest.TestCase):

    def test_unlinked_source_leg_does_not_route(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=False)
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-5', 2, bytes_3(DIAL_A_TG))
        self.assertEqual(routed, [])

    def test_linked_source_routes_only_own_reflector_bridge(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=True)
        bridges.update(_reflector_bridge(3100, 'SYSTEM-6', source_active=True))
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-5', 2, bytes_3(DIAL_A_TG))
        self.assertEqual(routed, ['#2350'])

    def test_linked_tg9_does_not_forward_to_unlinked_peers(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=True)
        idx = build_bridge_index(bridges)
        self.assertEqual(
            collect_group_route_bridges(
                bridges, idx, 'SYSTEM-5', 2, bytes_3(DIAL_A_TG)),
            ['#2350'])
        receivers = to_target_forward_systems(bridges['#2350'], 'SYSTEM-5')
        self.assertEqual(receivers, [])

    def test_linked_tg9_forwards_only_to_same_reflector_peers(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=True)
        bridges['#2350'][1]['ACTIVE'] = True  # peer also linked #2350
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-5', 2, bytes_3(DIAL_A_TG))
        self.assertEqual(routed, ['#2350'])
        receivers = to_target_forward_systems(bridges['#2350'], 'SYSTEM-5')
        self.assertEqual(receivers, ['SYSTEM-6'])

    def test_cross_reflector_peer_not_in_forward_list(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=True)
        bridges.update(_reflector_bridge(3100, 'SYSTEM-6', source_active=True))
        receivers = to_target_forward_systems(bridges['#2350'], 'SYSTEM-5')
        self.assertNotIn('SYSTEM-6', receivers)

    def test_obp_receives_on_linked_tg_not_nine(self):
        bridges = _reflector_bridge(
            2350, 'SYSTEM-5', source_active=True, obp='OBP-1')
        obp_entry = next(e for e in bridges['#2350'] if e['SYSTEM'] == 'OBP-1')
        self.assertEqual(obp_entry['TGID'], bytes_3(2350))
        self.assertNotEqual(obp_entry['TGID'], bytes_3(DIAL_A_TG))
        receivers = to_target_forward_systems(bridges['#2350'], 'SYSTEM-5')
        self.assertEqual(receivers, ['OBP-1'])

    def test_paired_numeric_bridge_only_when_present(self):
        bridges = _reflector_bridge(2350, 'SYSTEM-5', source_active=True)
        bridges['2350'] = copy.deepcopy(bridges['#2350'])
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-5', 2, bytes_3(DIAL_A_TG))
        self.assertEqual(set(routed), {'#2350', '2350'})

    def test_numeric_talkgroup_does_not_pair_to_reflector(self):
        """Direct group key on 23426 must not route via #23426 dial reflector."""
        bridges = _reflector_bridge(23426, 'SYSTEM-5', source_active=False)
        bridges['23426'] = [{
            'SYSTEM': 'SYSTEM-5',
            'TS': 2,
            'TGID': bytes_3(23426),
            'ACTIVE': True,
            'TO_TYPE': 'ON',
            'ON': [bytes_3(23426)],
        }]
        idx = build_bridge_index(bridges)
        routed = collect_group_route_bridges(
            bridges, idx, 'SYSTEM-5', 2, bytes_3(23426))
        self.assertEqual(routed, ['23426'])
        self.assertNotIn('#23426', routed)


if __name__ == '__main__':
    unittest.main()
