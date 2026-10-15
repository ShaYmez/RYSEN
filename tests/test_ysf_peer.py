"""YSF reflector peer: DN framing, AMBE regrouping, and rules TG stamping."""

from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest

from dmr_utils3 import bptc
from dmr_utils3.utils import bytes_3

from const import LC_OPT
import config
import ysf_protocol as ysf
from ysf_protocol import (
    CSD_COUNT,
    DT_VOICE_FR,
    FI_COMMUNICATION,
    FI_HEADER,
    FI_TERMINATOR,
    FRAME_SYNC,
    RADIO_CODE,
    RADIO_SERIAL,
    RADIO_TEXT,
    bits_to_bytes,
    bytes_to_bits,
    conv_encode,
    crc16,
    decode_dch,
    decode_dn_frame,
    decode_fich,
    decode_header_frame,
    encode_dch,
    encode_dn_frame,
    encode_header_frame,
    encode_fich,
    encode_voice,
    interleave_dibits,
    pack_fich,
    pack_poll,
    pack_unlink,
    pack_ysfd,
    parse_ysfd,
    pn_bits,
)
from ysf_voice import (
    SILENCE_49,
    YsfPeerMixin,
    _BURST_BITS,
    _HEAD_BITS,
    _TERM_BITS,
    _embed,
    ambe49_to_72,
    ambe72_from_payload,
    ambe72_to_49,
    build_dmrd,
    lc_payload,
    voice_payload,
)

ROOT = Path(__file__).resolve().parents[1]


class _Transport(object):
    def __init__(self, box):
        self.box = box

    def write(self, packet, addr):
        self.box.append(packet)


class _Peer(YsfPeerMixin):
    def __init__(self, subs=None):
        self._system = 'YSF-TEST'
        self._config = {
            'CALLSIGN': b'MX0SXC',
            'RADIO_ID': (2356427).to_bytes(4, 'big'),
            'MASTER_IP': '127.0.0.1',
            'MASTER_SOCKADDR': ('127.0.0.1', 42000),
            'DGID': 0,
            'TS': 2,
            'TGID': 9,
            'STATS': {},
        }
        self._CONFIG = {
            '_SUB_IDS': subs or {2350001: 'M0ABC'},
            '_LOCAL_SUBSCRIBER_IDS': {},
            'GLOBAL': {'PING_TIME': 10, 'MAX_MISSED': 3},
        }
        self.box = []
        self.captured = []
        self.init_ysf()
        self._ysf_immediate = True
        self.transport = _Transport(self.box)

    def dmrd_received(self, *args):
        self.captured.append(args[-1])


def _voice_fr_frame():
    fich = encode_fich(pack_fich(FI_COMMUNICATION, 0, 5, 0, dt=DT_VOICE_FR))
    dch = encode_dch(bytes_to_bits(b'ALL       '))
    bits = bytes_to_bits(FRAME_SYNC) + fich
    for index in range(5):
        bits.extend(dch[index * 40:(index + 1) * 40])
        bits.extend(encode_voice(SILENCE_49))
    return bits_to_bytes(bits)


def _dmrd(seq, kind, payload, stream=b'\x01\x02\x03\x04'):
    src = bytes_3(2350001)
    dst = bytes_3(9)
    peer = (2356427).to_bytes(4, 'big')
    return build_dmrd(seq, src, dst, peer, 2, kind, stream, payload)


class TestYsfProtocol(unittest.TestCase):

    def test_poll_and_unlink_are_14_bytes(self):
        poll = pack_poll('mx0sxc')
        self.assertEqual(poll, b'YSFPMX0SXC    ')
        self.assertEqual(len(pack_unlink('MX0SXC')), 14)
        self.assertTrue(pack_unlink(b'MX0SXC').startswith(b'YSFU'))

    def test_ysfd_round_trip_and_fich_dgid(self):
        ambe = [(index * 3) & 1 for index in range(49)]
        frame = encode_dn_frame([ambe] * 5, 2, 7, 'M0ABC', 'MX0SXC')
        packet = pack_ysfd('MX0SXC', 'M0ABC', 'ALL', 4, True, frame)
        self.assertEqual(len(packet), 155)
        parsed = parse_ysfd(packet)
        self.assertTrue(parsed['end'])
        self.assertEqual(parsed['seq'], 4)
        self.assertEqual(parsed['source'], b'M0ABC     ')
        decoded = decode_dn_frame(parsed['frame'])
        self.assertEqual(decoded['fich']['dt'], 2)
        self.assertEqual(decoded['fich']['dgid'], 7)
        self.assertEqual(decoded['fich']['fn'], 2)
        self.assertEqual(decoded['ambes'], [ambe] * 5)
        self.assertIsNotNone(decoded['dch'])

    def test_fich_without_dgid_disables_squelch_code(self):
        fich = decode_fich(encode_fich(pack_fich(FI_COMMUNICATION, 0, 5, 0)))
        self.assertEqual(fich['sql'], 1)
        self.assertEqual(fich['dgid'], 0)

    def test_voice_fr_has_no_dn_ambe(self):
        decoded = decode_dn_frame(_voice_fr_frame())
        self.assertEqual(decoded['fich']['dt'], DT_VOICE_FR)
        self.assertIsNone(decoded['ambes'])

    def test_dch_whitens_the_callsign_before_the_crc(self):
        info = bytes_to_bits(b'M0ABC     ')
        whitened = [bit ^ mask for bit, mask in zip(info, pn_bits(80))]
        coded = conv_encode(whitened + crc16(whitened))
        expect = ysf._dibits_to_bits(
            interleave_dibits(ysf._bits_to_dibits(coded), 5, 20))
        self.assertEqual(encode_dch(info), expect)
        self.assertEqual(bits_to_bytes(decode_dch(expect)), b'M0ABC     ')
        self.assertNotEqual(crc16(info), crc16(whitened))

    def test_outbound_csd_names_the_radio_systemx(self):
        silence = [SILENCE_49] * 5
        remark = encode_dn_frame(silence, 4, 0, 'M0ABC', 'MX0SXC')
        decoded = decode_dn_frame(remark)
        self.assertEqual(decoded['fich']['fn'], 4)
        self.assertEqual(decoded['fich']['ft'], CSD_COUNT - 1)
        self.assertEqual(bits_to_bytes(decoded['dch']), b'SYSTEMX   ')
        serial = encode_dn_frame(silence, 5, 0, 'M0ABC', 'MX0SXC')
        self.assertEqual(
            bits_to_bytes(decode_dn_frame(serial)['dch']),
            b'MX0SX' + RADIO_SERIAL)
        radio = encode_dn_frame(silence, 6, 0, 'M0ABC', 'MX0SXC')
        block = bits_to_bytes(decode_dn_frame(radio)['dch'])
        self.assertEqual(block[4], RADIO_CODE)
        self.assertNotEqual(block[4], 0x2B)
        self.assertEqual(block[5], 0x03)
        self.assertEqual(block[6], sum(block[:6]) & 0xFF)
        self.assertTrue(RADIO_TEXT.startswith(b'SYSTEMX'))

    def test_header_frame_carries_both_callsign_blocks(self):
        frame = encode_header_frame('ALL', 'M0ABC', 'MX0SXC', 'M0ABC', 0)
        self.assertEqual(len(frame), 120)
        decoded = decode_header_frame(frame)
        self.assertEqual(decoded['fich']['fi'], FI_HEADER)
        self.assertEqual(decoded['fich']['ft'], CSD_COUNT - 1)
        self.assertEqual(decoded['csd1'], b'ALL       M0ABC     ')
        self.assertEqual(decoded['csd2'], b'MX0SXC    M0ABC     ')


class TestYsfVoice(unittest.TestCase):

    def test_silence_survives_dmr_and_dn_packing(self):
        word = ambe49_to_72(SILENCE_49)
        self.assertEqual(word.hex(), 'acaa40200044408080')
        payload = voice_payload([word, word, word], _embed(0, None))
        self.assertEqual(len(payload), 33)
        for extracted in ambe72_from_payload(payload):
            self.assertEqual(ambe72_to_49(extracted), SILENCE_49)

    def test_five_dn_words_become_dmr_on_tg9_slot2(self):
        peer = _Peer()
        frame = encode_dn_frame([SILENCE_49] * 5, 0, 0, 'M0ABC', 'MX0SXC')
        packet = pack_ysfd('GB3ABC', 'M0ABC', 'ALL', 1, True, frame)
        peer.ysf_datagram_received(packet, ('127.0.0.1', 42000))
        self.assertGreaterEqual(len(peer.captured), 3)
        for burst in peer.captured:
            self.assertEqual(burst[:4], b'DMRD')
            self.assertEqual(burst[8:11], bytes_3(9))
            self.assertEqual(burst[15] & 0x80, 0x80)
            self.assertEqual(int.from_bytes(burst[5:8], 'big'), 2350001)
            self.assertEqual(len(burst), 55)
        self.assertEqual(peer.captured[0][15] & 0x2F, _HEAD_BITS)
        self.assertEqual(peer.captured[-1][15] & 0x2F, _TERM_BITS)

    def test_unknown_callsign_uses_radio_id(self):
        peer = _Peer()
        frame = encode_dn_frame([SILENCE_49] * 5, 0, 0, 'N0CALL', 'MX0SXC')
        packet = pack_ysfd('GB3ABC', 'N0CALL', 'ALL', 1, True, frame)
        peer.ysf_datagram_received(packet, ('127.0.0.1', 42000))
        self.assertEqual(int.from_bytes(peer.captured[0][5:8], 'big'), 2356427)

    def test_own_gateway_and_voice_fr_are_not_bridged(self):
        peer = _Peer()
        frame = encode_dn_frame([SILENCE_49] * 5, 0, 0, 'M0ABC', 'MX0SXC')
        own = pack_ysfd('MX0SXC', 'M0ABC', 'ALL', 1, True, frame)
        peer.ysf_datagram_received(own, ('127.0.0.1', 42000))
        foreign = pack_ysfd('GB3ABC', 'M0ABC', 'ALL', 1, False, _voice_fr_frame())
        peer.ysf_datagram_received(foreign, ('127.0.0.1', 42000))
        self.assertEqual(peer.captured, [])

    def test_voice_fr_end_does_not_close_a_dn_call(self):
        peer = _Peer()
        frame = encode_dn_frame([SILENCE_49] * 5, 0, 0, 'M0ABC', 'MX0SXC')
        dn = pack_ysfd('GB3ABC', 'M0ABC', 'ALL', 1, False, frame)
        peer.ysf_datagram_received(dn, ('127.0.0.1', 42000))
        held = len(peer.captured)
        self.assertGreater(held, 0)
        self.assertTrue(peer._rx_open)
        voice_fr = pack_ysfd('GB3ABC', 'M0ABC', 'ALL', 2, True, _voice_fr_frame())
        peer.ysf_datagram_received(voice_fr, ('127.0.0.1', 42000))
        self.assertEqual(len(peer.captured), held)
        self.assertTrue(peer._rx_open)
        self.assertFalse(peer._rx_ending)
        self.assertNotEqual(peer.captured[-1][15] & 0x2F, _TERM_BITS)

    def test_disconnected_reflector_is_resolved_again(self):
        peer = _Peer()
        stats = peer._config['STATS']
        stats['CONNECTION'] = 'NO'
        stats['DNS_TIME'] = 0
        peer._config['_MASTER_IP'] = 'ysf.example.net'
        peer._config['MASTER_IP'] = '1.2.3.4'
        peer._config['MASTER_PORT'] = 42000
        peer._config['MASTER_SOCKADDR'] = ('1.2.3.4', 42000)
        seen = []

        def resolve(host):
            seen.append(host)
            return '9.9.9.9'

        peer._ysf_resolve = resolve
        peer.ysf_maintenance_loop()
        self.assertEqual(seen, ['ysf.example.net'])
        self.assertEqual(peer._config['MASTER_IP'], '9.9.9.9')
        self.assertEqual(peer._config['MASTER_SOCKADDR'], ('9.9.9.9', 42000))
        peer.ysf_maintenance_loop()
        self.assertEqual(seen, ['ysf.example.net'])

        stats['CONNECTION'] = 'YES'
        stats['LAST_PING_ACK_TIME'] = 10 ** 12
        stats['DNS_TIME'] = 0
        peer._config['MASTER_IP'] = '1.2.3.4'
        peer.ysf_maintenance_loop()
        self.assertEqual(seen, ['ysf.example.net'])
        self.assertEqual(peer._config['MASTER_IP'], '1.2.3.4')

    def test_poll_ack_stamps_connected_and_logs_link(self):
        peer = _Peer()
        with self.assertLogs('ysf_voice', level='INFO') as caught:
            peer.ysf_datagram_received(pack_poll('GB7ABC'), ('127.0.0.1', 42000))
        self.assertEqual(
            caught.output,
            ['INFO:ysf_voice:(YSF-TEST) YSF reflector logged in 127.0.0.1:42000'])
        stats = peer._config['STATS']
        self.assertEqual(stats['CONNECTION'], 'YES')
        self.assertTrue(stats['CONNECTED'])
        first = stats['CONNECTED']
        peer.ysf_datagram_received(pack_poll('GB7ABC'), ('127.0.0.1', 42000))
        self.assertEqual(stats['CONNECTED'], first)

    def test_dmr_bursts_regroup_into_ysfd(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        header = _dmrd(0, _HEAD_BITS, lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD'))
        word = ambe49_to_72(SILENCE_49)
        bursts = []
        for position in range(2):
            payload = voice_payload([word, word, word], _embed(position, None))
            bursts.append(_dmrd(position + 1, _BURST_BITS[position], payload))
        term = _dmrd(3, _TERM_BITS, lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM'))
        for packet in [header] + bursts + [term]:
            peer.ysf_send_system(packet)
        self.assertEqual(len(peer.box), 4)
        opened = parse_ysfd(peer.box[0])
        voice = parse_ysfd(peer.box[1])
        tail = parse_ysfd(peer.box[2])
        closing = parse_ysfd(peer.box[3])
        self.assertEqual(decode_header_frame(opened['frame'])['fich']['fi'], FI_HEADER)
        self.assertFalse(opened['end'])
        self.assertEqual(voice['source'], b'M0ABC     ')
        self.assertEqual(voice['gateway'], b'MX0SXC    ')
        self.assertEqual(decode_dn_frame(voice['frame'])['ambes'], [SILENCE_49] * 5)
        # The sixth AMBE word is flushed with silence, then a terminator.
        self.assertFalse(tail['end'])
        self.assertEqual(decode_dn_frame(tail['frame'])['ambes'], [SILENCE_49] * 5)
        self.assertTrue(closing['end'])
        self.assertEqual(decode_dn_frame(closing['frame'])['fich']['fi'], FI_TERMINATOR)

    def test_new_stream_flushes_the_previous_one_immediately(self):
        peer = _Peer()
        peer._ysf_immediate = False

        class _Timer:
            def active(self):
                return True

            def cancel(self):
                self.cancelled = True

        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        word = ambe49_to_72(SILENCE_49)
        header = _dmrd(
            0, _HEAD_BITS,
            lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD'))
        payload = voice_payload([word, word, word], _embed(0, None))
        burst = _dmrd(1, _BURST_BITS[0], payload)
        peer.ysf_send_system(header)
        peer.ysf_send_system(burst)
        self.assertEqual(len(peer.box), 1)
        self.assertEqual(
            decode_header_frame(parse_ysfd(peer.box[0])['frame'])['fich']['fi'],
            FI_HEADER)
        timer = _Timer()
        peer._tx_timer = timer
        nxt = _dmrd(
            0, _HEAD_BITS,
            lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD'),
            stream=b'\x0a\x0b\x0c\x0d')
        peer.ysf_send_system(nxt)
        self.assertTrue(timer.cancelled)
        self.assertIsNone(peer._tx_timer)
        # Old voice, its terminator, then the new stream's header.
        self.assertEqual(len(peer.box), 4)
        flushed = parse_ysfd(peer.box[2])
        self.assertTrue(flushed['end'])
        self.assertEqual(flushed['source'], b'M0ABC     ')
        self.assertEqual(
            decode_header_frame(parse_ysfd(peer.box[3])['frame'])['fich']['fi'],
            FI_HEADER)
        self.assertEqual(peer._tx_stream, b'\x0a\x0b\x0c\x0d')
        self.assertFalse(peer._tx_ending)

    def test_terminator_after_full_group_sets_the_end_bit(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        word = ambe49_to_72(SILENCE_49)
        header = _dmrd(
            0, _HEAD_BITS,
            lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD'))
        peer.ysf_send_system(header)
        for position in range(5):
            payload = voice_payload([word, word, word], _embed(position % 6, None))
            peer.ysf_send_system(_dmrd(position + 1, _BURST_BITS[position % 6], payload))
        self.assertEqual(len(peer.box), 4)
        self.assertFalse(any(parse_ysfd(packet)['end'] for packet in peer.box))
        term = _dmrd(
            6, _TERM_BITS,
            lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM'))
        peer.ysf_send_system(term)
        self.assertEqual(len(peer.box), 5)
        closing = parse_ysfd(peer.box[-1])
        self.assertTrue(closing['end'])
        self.assertEqual(decode_dn_frame(closing['frame'])['fich']['fi'], FI_TERMINATOR)
        self.assertIsNone(peer._tx_stream)

    def test_header_only_terminator_sets_the_end_bit(self):
        peer = _Peer()
        src = bytes_3(2350001)
        dst = bytes_3(9)
        lc = b''.join((LC_OPT, dst, src))
        peer.ysf_send_system(_dmrd(
            0, _HEAD_BITS,
            lc_payload(bptc.encode_header_lc(lc), 'VOICE_LC_HEAD')))
        self.assertEqual(len(peer.box), 1)
        self.assertFalse(parse_ysfd(peer.box[0])['end'])
        peer.ysf_send_system(_dmrd(
            1, _TERM_BITS,
            lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM')))
        self.assertEqual(len(peer.box), 2)
        self.assertTrue(parse_ysfd(peer.box[1])['end'])
        self.assertEqual(
            decode_dn_frame(parse_ysfd(peer.box[1])['frame'])['fich']['fi'],
            FI_TERMINATOR)
        peer.ysf_send_system(_dmrd(
            2, _TERM_BITS,
            lc_payload(bptc.encode_terminator_lc(lc), 'VOICE_LC_TERM')))
        self.assertEqual(len(peer.box), 2)

    def test_nonzero_dgid_drops_other_rooms(self):
        peer = _Peer()
        peer._config['DGID'] = 7
        match = pack_ysfd(
            'GB3ABC', 'M0ABC', 'ALL', 1, False,
            encode_dn_frame([SILENCE_49] * 5, 0, 7, 'M0ABC', 'MX0SXC'))
        other = pack_ysfd(
            'GB3ABC', 'M0ABC', 'ALL', 2, True,
            encode_dn_frame([SILENCE_49] * 5, 0, 3, 'M0ABC', 'MX0SXC'))
        peer.ysf_datagram_received(match, ('127.0.0.1', 42000))
        held = len(peer.captured)
        self.assertGreater(held, 0)
        self.assertTrue(peer._rx_open)
        peer.ysf_datagram_received(other, ('127.0.0.1', 42000))
        self.assertEqual(len(peer.captured), held)
        self.assertTrue(peer._rx_open)
        self.assertFalse(peer._rx_ending)

        open_room = _Peer()
        foreign = pack_ysfd(
            'GB3ABC', 'M0ABC', 'ALL', 1, True,
            encode_dn_frame([SILENCE_49] * 5, 0, 7, 'M0ABC', 'MX0SXC'))
        open_room.ysf_datagram_received(foreign, ('127.0.0.1', 42000))
        self.assertGreater(len(open_room.captured), 0)


class TestYsfConfig(unittest.TestCase):

    def test_stanza_parses_without_homebrew_fields(self):
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

[YSF-ROOM]
MODE: YSFPEER
ENABLED: True
IP:
PORT: 54300
MASTER_IP: 127.0.0.1
MASTER_PORT: 42000
CALLSIGN: MX0SXC
RADIO_ID: 2356427
DESCRIPTION: Room
'''
        with NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.cfg', delete=False) as handle:
            handle.write(content)
            path = Path(handle.name)
        try:
            parsed = config.build_config(str(path))
        finally:
            path.unlink(missing_ok=True)
        system = parsed['SYSTEMS']['YSF-ROOM']
        self.assertEqual(system['MODE'], 'YSFPEER')
        self.assertEqual(system['IP'], '')
        self.assertEqual(system['CALLSIGN'], b'MX0SXC    ')
        self.assertEqual(system['DGID'], 0)
        self.assertEqual(system['MASTER_SOCKADDR'], ('127.0.0.1', 42000))
        self.assertEqual(int.from_bytes(system['RADIO_ID'], 'big'), 2356427)

    def test_both_bridges_construct_a_ysf_router(self):
        for name in ('bridge.py', 'bridge_master.py'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn("['MODE'] == 'YSFPEER'", text)
            self.assertIn('routerYSF(', text)
        from bridge import routerYSF as bridge_router
        from bridge_master import routerYSF as master_router
        cfg = {
            'SYSTEMS': {
                'YSF-ROOM': {
                    'MODE': 'YSFPEER',
                    'CALLSIGN': b'MX0SXC    ',
                    'RADIO_ID': (2356427).to_bytes(4, 'big'),
                    'MASTER_IP': '127.0.0.1',
                    'MASTER_SOCKADDR': ('127.0.0.1', 42000),
                    'DGID': 0,
                    'TS': 2,
                    'TGID': 9,
                    'STATS': {},
                    'ANNOUNCEMENT_LANGUAGE': 'en_GB',
                    'USE_ACL': False,
                    'GROUP_HANGTIME': 5,
                },
            },
        }
        for cls in (bridge_router, master_router):
            router = cls('YSF-ROOM', cfg, None)
            self.assertEqual(router.datagramReceived, router.ysf_datagram_received)
            self.assertEqual(router.send_system, router.ysf_send_system)
            self.assertIn(1, router.STATUS)
            self.assertIn(2, router.STATUS)


if __name__ == '__main__':
    unittest.main()
