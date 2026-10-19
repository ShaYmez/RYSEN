"""NXDN reflector peer: framing, AMBE regrouping, and rules TG stamping."""

from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest

from dmr_utils3 import bptc
from dmr_utils3.utils import bytes_3

from const import LC_OPT
import config
import nxdn_protocol as nxdn
from nxdn_protocol import (
    FLAG_DATA,
    FLAG_END,
    FLAG_GROUP,
    FLAG_HEADER,
    LICH_HEADER,
    LICH_VOICE,
    MESSAGE_TX_REL,
    MESSAGE_VCALL,
    SACCH_STRUCTURES,
    audio_words,
    crc6,
    encode_sacch,
    pack_data,
    pack_half,
    pack_poll,
    pack_unlink,
    parse_data,
    signalling_payload,
    unpack_half,
    voice_payload,
)
from nxdn_aliases import (
    attach_callsign_index,
    attach_nxdn_maps,
    dmr_id_for_nxdn,
    nxdn_id_for_dmr,
    parse_nxdn_csv,
)
from nxdn_voice import NxdnPeerMixin
from ysf_voice import (
    SILENCE_49,
    _BURST_BITS,
    _HEAD_BITS,
    _TERM_BITS,
    _embed,
    ambe49_to_72,
    ambe72_to_49,
    build_dmrd,
    lc_payload,
    voice_payload as dmr_voice_payload,
)

ROOT = Path(__file__).resolve().parents[1]


class _Transport(object):
    def __init__(self, box):
        self.box = box

    def write(self, packet, addr):
        self.box.append(packet)


class _Peer(NxdnPeerMixin):
    def __init__(self):
        self._system = 'NXDN-TEST'
        self._config = {
            'CALLSIGN': b'MX0SXC',
            'TALKGROUP': 65000,
            'NXDN_ID': 12345,
            'RAN': 0,
            'RADIO_ID': (2356427).to_bytes(4, 'big'),
            'MASTER_IP': '127.0.0.1',
            'MASTER_SOCKADDR': ('127.0.0.1', 41400),
            'STATS': {},
        }
        self._CONFIG = {'GLOBAL': {'PING_TIME': 10, 'MAX_MISSED': 3}}
        self.box = []
        self.captured = []
        self.init_nxdn()
        self._nxdn_immediate = True
        self.transport = _Transport(self.box)

    def dmrd_received(self, *args):
        self.captured.append(args[-1])


def _dmrd(seq, kind, payload, stream=b'\x01\x02\x03\x04'):
    src = bytes_3(2350001)
    dst = bytes_3(9)
    peer = (2356427).to_bytes(4, 'big')
    return build_dmrd(seq, src, dst, peer, 2, kind, stream, payload)


def _silence_burst(seq, position):
    word = ambe49_to_72(SILENCE_49)
    payload = dmr_voice_payload([word, word, word], _embed(position % 6, None))
    return _dmrd(seq, _BURST_BITS[position % 6], payload)


class TestNxdnProtocol(unittest.TestCase):

    def test_poll_and_unlink_are_17_bytes(self):
        poll = pack_poll('mx0sxc', 65000)
        self.assertEqual(len(poll), 17)
        self.assertEqual(poll, b'NXDNPMX0SXC    ' + (65000).to_bytes(2, 'big'))
        unlink = pack_unlink('MX0SXC', 65000)
        self.assertEqual(len(unlink), 17)
        self.assertTrue(unlink.startswith(b'NXDNU'))
        self.assertEqual(unlink[5:15], b'MX0SXC    ')

    def test_data_frame_is_43_bytes(self):
        payload = signalling_payload(MESSAGE_VCALL, 12345, 65000)
        packet = pack_data(12345, 65000, FLAG_GROUP | FLAG_HEADER, payload)
        self.assertEqual(len(packet), 43)
        parsed = parse_data(packet)
        self.assertEqual(parsed['src'], 12345)
        self.assertEqual(parsed['dst'], 65000)
        self.assertTrue(parsed['group'])
        self.assertFalse(parsed['end'])
        self.assertEqual(parsed['payload'][0], LICH_HEADER)
        self.assertEqual(parsed['payload'][0], 0x81)
        self.assertEqual(parsed['payload'][5], MESSAGE_VCALL)
        self.assertEqual(parsed['payload'][5:19], parsed['payload'][19:33])
        self.assertEqual(parsed['payload'][8:10], (12345).to_bytes(2, 'big'))
        self.assertEqual(parsed['payload'][10:12], (65000).to_bytes(2, 'big'))

    def test_trailer_sets_the_end_message(self):
        payload = signalling_payload(MESSAGE_TX_REL, 12345, 65000)
        self.assertEqual(payload[0], 0x81)
        self.assertEqual(payload[5], 0x08)
        packet = pack_data(12345, 65000, FLAG_GROUP | FLAG_END, payload)
        self.assertTrue(parse_data(packet)['end'])

    def test_sacch_carries_ran_and_crc6(self):
        sacch = encode_sacch(0, 0, [0] * 18)
        self.assertEqual(len(sacch), 4)
        self.assertEqual(sacch[0] & 0x3F, 0)
        self.assertEqual(sacch[0] >> 6, 0)
        got = 0
        for index in range(6):
            got = (got << 1) | nxdn._read_bit(sacch, 26 + index)
        self.assertEqual(got, crc6(sacch, 26))
        other = encode_sacch(7, 3, [1] * 18)
        self.assertEqual(other[0] & 0x3F, 7)
        self.assertEqual(other[0] >> 6, 3)
        self.assertNotEqual(sacch, other)

    def test_silence_round_trip(self):
        half = pack_half(SILENCE_49, SILENCE_49)
        self.assertEqual(len(half), 14)
        first, second = unpack_half(half)
        self.assertEqual(first, list(SILENCE_49))
        self.assertEqual(second, list(SILENCE_49))
        self.assertEqual(ambe49_to_72(first), bytes.fromhex('acaa40200044408080'))
        packed = voice_payload([SILENCE_49] * 4, 12345, 65000, 0)
        self.assertEqual(packed[0], LICH_VOICE)
        self.assertEqual(audio_words(packed), [list(SILENCE_49)] * 4)
        self.assertEqual((packed[1] >> 6) & 3, SACCH_STRUCTURES[0])


class TestNxdnVoice(unittest.TestCase):

    def test_dmr_bursts_regroup_into_four_word_frames(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        header = _dmrd(0, _HEAD_BITS, lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD'))
        bursts = [_silence_burst(position + 1, position) for position in range(2)]
        term = _dmrd(3, _TERM_BITS, lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM'))
        for packet in [header] + bursts + [term]:
            peer.nxdn_send_system(packet)
        self.assertEqual(len(peer.box), 4)
        opened = parse_data(peer.box[0])
        voice = parse_data(peer.box[1])
        tail = parse_data(peer.box[2])
        closing = parse_data(peer.box[3])
        self.assertEqual(opened['src'], 12345)
        self.assertEqual(opened['dst'], 65000)
        self.assertEqual(opened['flags'], FLAG_GROUP | FLAG_HEADER)
        self.assertEqual(opened['payload'][5], MESSAGE_VCALL)
        self.assertNotEqual(opened['src'], 2350001)
        self.assertEqual(audio_words(voice['payload']), [list(SILENCE_49)] * 4)
        self.assertEqual(voice['flags'], FLAG_GROUP)
        self.assertEqual(audio_words(tail['payload']), [list(SILENCE_49)] * 4)
        self.assertTrue(closing['end'])
        self.assertEqual(closing['payload'][5], MESSAGE_TX_REL)
        self.assertIsNone(peer._tx_stream)

    def test_sacch_structure_cycles_across_voice_frames(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        peer.nxdn_send_system(_dmrd(
            0, _HEAD_BITS, lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD')))
        for position in range(6):
            peer.nxdn_send_system(_silence_burst(position + 1, position))
        structures = [(parse_data(packet)['payload'][1] >> 6) for packet in peer.box[1:5]]
        self.assertEqual(tuple(structures), SACCH_STRUCTURES)

    def test_late_terminator_does_not_open_a_stream(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        peer.nxdn_send_system(_dmrd(
            0, _HEAD_BITS, lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD')))
        peer.nxdn_send_system(_dmrd(
            1, _TERM_BITS, lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM')))
        self.assertEqual(len(peer.box), 2)
        self.assertTrue(parse_data(peer.box[1])['end'])
        peer.nxdn_send_system(_dmrd(
            2, _TERM_BITS, lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM')))
        self.assertEqual(len(peer.box), 2)

    def test_inbound_voice_lands_on_slot_2_talkgroup_9(self):
        peer = _Peer()
        payload = voice_payload([SILENCE_49] * 4, 100, 65000, 0)
        peer.nxdn_datagram_received(
            pack_data(100, 65000, FLAG_GROUP, payload), ('127.0.0.1', 41400))
        self.assertGreaterEqual(len(peer.captured), 2)
        header = peer.captured[0]
        voice = peer.captured[1]
        self.assertEqual(header[5:8], bytes_3(100))
        self.assertEqual(header[8:11], bytes_3(9))
        self.assertEqual(voice[8:11], bytes_3(9))
        self.assertTrue(voice[15] & 0x80)
        self.assertEqual(voice[11:15], (2356427).to_bytes(4, 'big'))
        words = []
        from ysf_voice import ambe72_from_payload
        for word in ambe72_from_payload(voice[20:53]):
            words.append(ambe72_to_49(word))
        self.assertEqual(words, [list(SILENCE_49)] * 3)
        peer.nxdn_datagram_received(
            pack_data(100, 65000, FLAG_GROUP | FLAG_END, signalling_payload(
                MESSAGE_TX_REL, 100, 65000)),
            ('127.0.0.1', 41400))
        self.assertFalse(peer._rx_open)
        self.assertEqual(peer.captured[-1][15] & 0x0F, 0x02)

    def test_data_and_own_echo_are_ignored(self):
        peer = _Peer()
        data = bytearray(33)
        data[0] = 0x90
        peer.nxdn_datagram_received(
            pack_data(100, 65000, FLAG_DATA, bytes(data)), ('127.0.0.1', 41400))
        echo = voice_payload([SILENCE_49] * 4, 12345, 65000, 0)
        peer.nxdn_datagram_received(
            pack_data(12345, 65000, FLAG_GROUP, echo), ('127.0.0.1', 41400))
        self.assertEqual(peer.captured, [])
        self.assertFalse(peer._rx_open)

    def test_a_datagram_from_the_reflector_logs_the_link_in(self):
        peer = _Peer()
        peer.nxdn_datagram_received(pack_poll('OTHER', 65000), ('127.0.0.1', 41400))
        self.assertEqual(peer._config['STATS']['CONNECTION'], 'YES')
        self.assertIsNotNone(peer._config['STATS']['CONNECTED'])
        peer.nxdn_datagram_received(pack_poll('OTHER', 65000), ('10.0.0.8', 41400))
        fresh = _Peer()
        fresh.nxdn_datagram_received(pack_poll('OTHER', 65000), ('10.0.0.8', 41400))
        self.assertEqual(fresh._config['STATS'].get('CONNECTION', 'NO'), 'NO')


class TestNxdnConfig(unittest.TestCase):

    def test_stanza_parses_without_slots_or_tgid(self):
        content = '''
[GLOBAL]
PATH: ./
PING_TIME: 10
MAX_MISSED: 3
USE_ACL: True
REG_ACL: DENY:0-100000
SUB_ACL: DENY:0-100000
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
GEN_STAT_BRIDGES: True
ALLOW_NULL_PASSPHRASE: True
ANNOUNCEMENT_LANGUAGES:
SERVER_ID: 0
DATA_GATEWAY: False
VALIDATE_SERVER_IDS: False

[REPORTS]
REPORT: False
REPORT_INTERVAL: 30
REPORT_PORT: 4321
REPORT_CLIENTS: *

[LOGGER]
LOG_FILE:
LOG_HANDLERS: console-timed
LOG_LEVEL: INFO
LOG_NAME: RYSEN

[ALIASES]
TRY_DOWNLOAD: False
PATH: ./json/
PEER_FILE: peer_ids.json
SUBSCRIBER_FILE: subscriber_ids.json
LOCAL_SUBSCRIBER_FILE: local_subscriber_ids.json
TGID_FILE: talkgroup_ids.json
PEER_URL: http://127.0.0.1/peer_ids.json
SUBSCRIBER_URL: http://127.0.0.1/subscriber_ids.json
TGID_URL: http://127.0.0.1/talkgroup_ids.json
SERVER_ID_URL: http://127.0.0.1/server_ids.tsv
SERVER_ID_FILE: server_ids.tsv
STALE_DAYS: 14
SUB_MAP_FILE: sub_map.pkl

[NXDN-ROOM]
MODE: NXDNPEER
ENABLED: True
IP:
PORT: 41410
MASTER_IP: 127.0.0.1
MASTER_PORT: 41400
CALLSIGN: MX0SXC
TALKGROUP: 65000
NXDN_ID: 12345
RADIO_ID: 2356427
DESCRIPTION: Room

[NXDN-OFF]
MODE: NXDNPEER
ENABLED: False
IP:
PORT: 41411
MASTER_IP: 127.0.0.1
MASTER_PORT: 41400
CALLSIGN: MX0SXC
TALKGROUP: 1
NXDN_ID: 1
RADIO_ID: 1
DESCRIPTION: Off
'''
        with NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.cfg', delete=False) as handle:
            handle.write(content)
            path = Path(handle.name)
        try:
            parsed = config.build_config(str(path))
        finally:
            path.unlink(missing_ok=True)
        self.assertNotIn('NXDN-OFF', parsed['SYSTEMS'])
        system = parsed['SYSTEMS']['NXDN-ROOM']
        self.assertEqual(system['MODE'], 'NXDNPEER')
        self.assertEqual(system['IP'], '')
        self.assertEqual(system['CALLSIGN'], b'MX0SXC    ')
        self.assertEqual(system['TALKGROUP'], 65000)
        self.assertEqual(system['NXDN_ID'], 12345)
        self.assertEqual(system['RAN'], 0)
        self.assertEqual(system['MASTER_SOCKADDR'], ('127.0.0.1', 41400))
        self.assertEqual(int.from_bytes(system['RADIO_ID'], 'big'), 2356427)
        self.assertNotIn('TGID', system)
        self.assertNotIn('SLOTS', system)
        self.assertIsNone(system['STATS']['CONNECTED'])

    def test_both_bridges_construct_an_nxdn_router(self):
        for name in ('bridge.py', 'bridge_master.py'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn("['MODE'] == 'NXDNPEER'", text)
            self.assertIn('routerNXDN(', text)
            self.assertLess(text.index('class routerHBP'), text.index('class routerNXDN'))
        from bridge import routerNXDN as bridge_router
        from bridge_master import routerNXDN as master_router
        cfg = {
            'SYSTEMS': {
                'NXDN-ROOM': {
                    'MODE': 'NXDNPEER',
                    'CALLSIGN': b'MX0SXC    ',
                    'TALKGROUP': 65000,
                    'NXDN_ID': 12345,
                    'RADIO_ID': (2356427).to_bytes(4, 'big'),
                    'MASTER_IP': '127.0.0.1',
                    'MASTER_SOCKADDR': ('127.0.0.1', 41400),
                    'STATS': {},
                    'ANNOUNCEMENT_LANGUAGE': 'en_GB',
                    'USE_ACL': False,
                    'GROUP_HANGTIME': 5,
                },
            },
        }
        for cls in (bridge_router, master_router):
            router = cls('NXDN-ROOM', cfg, None)
            self.assertEqual(router.datagramReceived, router.nxdn_datagram_received)
            self.assertEqual(router.send_system, router.nxdn_send_system)
            self.assertEqual(router._slot(), 2)
            self.assertEqual(router._tgid(), bytes_3(9))
            self.assertIn(1, router.STATUS)
            self.assertIn(2, router.STATUS)


class TestNxdnAliases(unittest.TestCase):

    def test_csv_and_callsign_join(self):
        by_id, by_call = parse_nxdn_csv(
            'RADIO_ID,CALLSIGN,FIRST_NAME\n'
            '2425,M0VUB,Shane\n'
            '4720,G4XTZ,Alan\n'
            '1272,M0JKT,Freestar\n'
        )
        self.assertEqual(by_id[2425], 'M0VUB')
        self.assertEqual(by_call['G4XTZ'], 4720)
        config = {
            '_SUB_IDS': {2345875: 'M0VUB', 2345370: 'G4XTZ', 2340189: 'M0JKT'},
            '_LOCAL_SUBSCRIBER_IDS': {},
        }
        attach_nxdn_maps(config, by_id)
        attach_callsign_index(config)
        self.assertEqual(nxdn_id_for_dmr(config, 2345875, 1272), 2425)
        self.assertEqual(dmr_id_for_nxdn(config, 4720, 4720), 2345370)
        self.assertEqual(nxdn_id_for_dmr(config, 999999, 1272), 1272)
        self.assertEqual(dmr_id_for_nxdn(config, 99, 99), 99)

    def test_mapped_over_uses_talker_nxdn_id(self):
        peer = _Peer()
        peer._CONFIG.update({
            '_SUB_IDS': {2350001: 'M0VUB'},
            '_LOCAL_SUBSCRIBER_IDS': {},
        })
        attach_nxdn_maps(peer._CONFIG, {2425: 'M0VUB'})
        attach_callsign_index(peer._CONFIG)
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        peer.nxdn_send_system(_dmrd(
            0, _HEAD_BITS, lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD')))
        self.assertEqual(parse_data(peer.box[0])['src'], 2425)

    def test_inbound_nxdn_id_becomes_dmr_id(self):
        peer = _Peer()
        peer._CONFIG.update({
            '_SUB_IDS': {2345370: 'G4XTZ'},
            '_LOCAL_SUBSCRIBER_IDS': {},
        })
        attach_nxdn_maps(peer._CONFIG, {4720: 'G4XTZ'})
        attach_callsign_index(peer._CONFIG)
        payload = voice_payload([SILENCE_49] * 4, 4720, 65000, 0)
        peer.nxdn_datagram_received(
            pack_data(4720, 65000, FLAG_GROUP, payload), ('127.0.0.1', 41400))
        self.assertEqual(peer.captured[0][5:8], bytes_3(2345370))


if __name__ == '__main__':
    unittest.main()
