#!/usr/bin/env python3
###############################################################################
#   Talker Alias (ETSI TS 102 361-2 FLCO 0x04-0x07) and Homebrew DMRA
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################
"""Carry a transmitting radio's talker alias across a bridged voice call.

Homebrew hotspots send the alias as a DMRA datagram. Radios and current
MMDVMHost display the alias from embedded link control in the voice
superframe, which the bridge otherwise replaces with the talkgroup LC.
This module decodes either source, and rebuilds 7-bit talker-alias
embedded LC so the destination still has a group voice LC on the first
superframe and on a regular cadence after that.
"""

import configparser
import logging
import time

from bitarray import bitarray
from dmr_utils3 import bptc, crc, hamming
from dmr_utils3.utils import int_id

from const import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE

logger = logging.getLogger('RYSEN.talker_alias')

FLCO_HEADER = 0x04
FLCO_BLOCK1 = 0x05
FLCO_BLOCK2 = 0x06
FLCO_BLOCK3 = 0x07
_TA_FLCOS = (FLCO_HEADER, FLCO_BLOCK1, FLCO_BLOCK2, FLCO_BLOCK3)

_HEARD_TTL = 600.0
_MAX_CHARS = 31
# A missing voice terminator must not pin a repeat schedule until the cache
# overflows. Silence longer than this means the over has ended.
_REPEAT_IDLE = 3.0
_repeat = {}

_enabled = True
_tables = []
_heard = {}
_assemblers = {}
_announced = {}


def set_enabled(flag):
    global _enabled
    _enabled = bool(flag)


def set_tables(*tables):
    """Subscriber alias maps, first match wins. Keys are integer DMR IDs."""
    global _tables
    _tables = [table for table in tables if table]


def enabled():
    return _enabled


def refresh_from_file(path):
    """Re-read ``[GLOBAL] TALKER_ALIAS`` and apply it. None if the file is unreadable."""
    if not path:
        return None
    parser = configparser.ConfigParser()
    try:
        if not parser.read(path):
            return None
        if not parser.has_section('GLOBAL'):
            return None
        flag = parser.getboolean('GLOBAL', 'TALKER_ALIAS', fallback=True)
    except Exception:
        logger.debug('Talker Alias config refresh failed', exc_info=True)
        return None
    set_enabled(flag)
    return flag


def _as_int(src):
    if isinstance(src, (bytes, bytearray)):
        return int_id(bytes(src))
    return int(src)


def clean_alias(text):
    """Printable ASCII, collapsed spaces, capped at the 5-bit ETSI length."""
    if not isinstance(text, str):
        return ''
    chars = []
    for ch in text:
        code = ord(ch)
        if ch == ' ' or 32 < code < 127:
            chars.append(ch)
    collapsed = ' '.join(''.join(chars).split())
    return collapsed[:_MAX_CHARS]


def _from_record(val):
    if isinstance(val, str):
        return clean_alias(val)
    if isinstance(val, dict):
        call = val.get('callsign') or val.get('CALLSIGN') or ''
        name = val.get('name') or val.get('fname') or val.get('NAME') or ''
        return clean_alias('{} {}'.format(call, name).strip())
    return ''


def heard_alias(src):
    item = _heard.get(_as_int(src))
    if not item:
        return ''
    text, seen = item
    if time.monotonic() - seen > _HEARD_TTL:
        return ''
    return text


def resolve(src):
    """Radio-sent alias, otherwise the subscriber callsign. Empty if neither."""
    if not _enabled:
        return ''
    sid = _as_int(src)
    heard = heard_alias(sid)
    if heard:
        return heard
    for table in _tables:
        val = table.get(sid)
        if val is None:
            val = table.get(str(sid))
        text = _from_record(val)
        if text and not text.isdigit():
            return text
    return ''


def _remember(src, text):
    text = clean_alias(text)
    if not text or text.isdigit():
        return ''
    _heard[_as_int(src)] = (text, time.monotonic())
    return text


def _announce(src, text):
    sid = _as_int(src)
    if _announced.get(sid) == text:
        return
    _announced[sid] = text
    logger.info('Talker Alias %s: %s', sid, text)


class _Assembler:
    def __init__(self):
        self.buf = bytearray(28)
        self.mask = 0

    def add(self, block, payload):
        if block > 3 or len(payload) < 7:
            return ''
        if block == 0 and (self.mask & 1):
            self.buf = bytearray(28)
            self.mask = 0
        start = block * 7
        self.buf[start:start + 7] = payload[:7]
        self.mask |= 1 << block
        return _decode_buffer(self.buf, _contiguous_bytes(self.mask)) or ''


def _contiguous_bytes(mask):
    blocks = 0
    while mask & (1 << blocks):
        blocks += 1
    return blocks * 7


def _decode_buffer(buf, available):
    """MMDVM CDMRTA layout. Returns text once every declared character is present."""
    if available < 1:
        return None
    fmt = (buf[0] >> 6) & 0x03
    size = (buf[0] >> 1) & 0x1F
    if size < 1 or size > _MAX_CHARS:
        return None
    if fmt == 0:
        need = (7 + (size * 7) + 7) // 8
        if available < need:
            return None
        chars = bytearray()
        acc = 0
        filled = 0
        for index in range(min(available, 32)):
            if len(chars) >= size:
                break
            byte = buf[index]
            for shift in range(7, -1, -1):
                acc = ((acc << 1) | ((byte >> shift) & 1)) & 0xFF
                filled += 1
                if filled == 7:
                    if index > 0:
                        chars.append(acc & 0x7F)
                    filled = 0
                    acc = 0
                    if len(chars) >= size:
                        break
        if len(chars) < size:
            return None
        return clean_alias(chars[:size].decode('ascii', 'replace'))
    if fmt in (1, 2):
        if available < 1 + size:
            return None
        raw = bytes(buf[1:1 + size])
        encoding = 'utf-8' if fmt == 2 else 'latin-1'
        return clean_alias(raw.decode(encoding, 'replace'))
    if fmt == 3:
        if available < 1 + (size * 2):
            return None
        chars = bytearray()
        for index in range(size):
            hi = buf[2 * index + 1]
            lo = buf[2 * index + 2]
            chars.append(lo if hi == 0 else ord('?'))
        return clean_alias(chars.decode('ascii', 'replace'))
    return None


def _assembler_for(src):
    sid = _as_int(src)
    box = _assemblers.get(sid)
    if box is None:
        box = _Assembler()
        _assemblers[sid] = box
    return box


def note_dmra(packet):
    """Learn an alias from a hotspot DMRA datagram. Both lengths are accepted.

    15 bytes: current MMDVMHost (radio id, block, 7 data bytes).
    19 bytes: repeater id, radio id, block, 7 data bytes.
    """
    if not _enabled or len(packet) < 15 or packet[:4] != b'DMRA':
        return ''
    if len(packet) >= 19:
        src = packet[8:11]
        block = packet[11]
        data = packet[12:19]
    else:
        src = packet[4:7]
        block = packet[7]
        data = packet[8:15]
    if block > 3 or len(data) < 7:
        return ''
    text = _assembler_for(src).add(block, data)
    return _remember(src, text) if text else ''


def note_embedded(src, frame_type, vseq, payload):
    """Learn an alias from voice bursts B-E before the bridge rewrites them."""
    if (not _enabled or frame_type != HBPF_VOICE or vseq not in (1, 2, 3, 4)
            or not payload or len(payload) < 19):
        return ''
    bits = bitarray(endian='big')
    bits.frombytes(payload[:33] if len(payload) >= 33 else payload)
    if len(bits) < 148:
        return ''
    box = _assembler_for(src)
    frags = getattr(box, 'frags', None)
    if frags is None:
        box.frags = {}
        frags = box.frags
    frags[vseq] = bits[116:148]
    if any(index not in frags for index in (1, 2, 3, 4)):
        return ''
    try:
        lc = bptc.decode_emblc(frags[1] + frags[2] + frags[3] + frags[4])
    except Exception:
        box.frags = {}
        return ''
    box.frags = {}
    if len(lc) < 9:
        return ''
    flco = lc[0] & 0x3F
    if flco not in _TA_FLCOS:
        return ''
    text = box.add(flco - FLCO_HEADER, lc[2:9])
    return _remember(src, text) if text else ''


def encode_7bit(text):
    """7-bit ETSI talker alias. Returns 7-byte blocks (header, then 1..3)."""
    cleaned = clean_alias(text)
    if not cleaned:
        return []
    size = len(cleaned)
    bits = [0, 0]
    for shift in range(4, -1, -1):
        bits.append((size >> shift) & 1)
    for ch in cleaned.encode('ascii', 'replace'):
        value = ch if 32 <= ch < 127 else ord('?')
        for shift in range(6, -1, -1):
            bits.append((value >> shift) & 1)
    while len(bits) % 8:
        bits.append(0)
    raw = bytearray()
    for index in range(0, len(bits), 8):
        byte = 0
        for bit in bits[index:index + 8]:
            byte = (byte << 1) | bit
        raw.append(byte)
    while len(raw) % 7:
        raw.append(0)
    return [bytes(raw[index:index + 7]) for index in range(0, len(raw), 7)]


def encode_lc_blocks(text):
    """9-byte full LC words, one per talker-alias block."""
    blocks = []
    for index, payload in enumerate(encode_7bit(text)):
        blocks.append(bytes([FLCO_HEADER + index, 0x00]) + payload)
    return blocks


def encode_emblc(lc):
    """BPTC embedded LC.

    dmr_utils3 repeats matrix bit 24 in burst D and drops bit 25. That one
    bit sits in the talker-alias length, so a radio truncates the callsign.
    Voice LC continues to use the library encoder.
    """
    checksum = crc.csum5(lc)
    matrix = bitarray(endian='big')
    matrix.frombytes(lc)
    matrix.insert(32, checksum[0])
    matrix.insert(43, checksum[1])
    matrix.insert(54, checksum[2])
    matrix.insert(65, checksum[3])
    matrix.insert(76, checksum[4])
    for index in range(0, 112, 16):
        parity = hamming.enc_16114(matrix[index:index + 11])
        for offset, bit in enumerate(parity):
            matrix.insert(index + 11 + offset, bit)
    for index in range(16):
        parity = (
            matrix[index]
            ^ matrix[index + 16]
            ^ matrix[index + 32]
            ^ matrix[index + 48]
            ^ matrix[index + 64]
            ^ matrix[index + 80]
            ^ matrix[index + 96]
        )
        matrix.insert(index + 112, parity)

    def row(indexes):
        frag = bitarray(endian='big')
        frag.extend(matrix[index] for index in indexes)
        return frag

    return {
        1: row((0, 16, 32, 48, 64, 80, 96, 112,
                1, 17, 33, 49, 65, 81, 97, 113,
                2, 18, 34, 50, 66, 82, 98, 114,
                3, 19, 35, 51, 67, 83, 99, 115)),
        2: row((4, 20, 36, 52, 68, 84, 100, 116,
                5, 21, 37, 53, 69, 85, 101, 117,
                6, 22, 38, 54, 70, 86, 102, 118,
                7, 23, 39, 55, 71, 87, 103, 119)),
        3: row((8, 24, 40, 56, 72, 88, 104, 120,
                9, 25, 41, 57, 73, 89, 105, 121,
                10, 26, 42, 58, 74, 90, 106, 122,
                11, 27, 43, 59, 75, 91, 107, 123)),
        4: row((12, 28, 44, 60, 76, 92, 108, 124,
                13, 29, 45, 61, 77, 93, 109, 125,
                14, 30, 46, 62, 78, 94, 110, 126,
                15, 31, 47, 63, 79, 95, 111, 127)),
    }


def encode_emb_blocks(text):
    """Embedded-LC fragment maps, one BPTC superframe per alias block."""
    return [encode_emblc(lc) for lc in encode_lc_blocks(text)]


class AliasSchedule:
    """Rotate group/unit voice LC with talker-alias blocks across superframes.

    The first superframe of an over stays voice LC so late entry still learns
    the talkgroup. Alias blocks follow, then voice LC returns between them.
    """

    def __init__(self, src):
        self.reset(src)

    def reset(self, src):
        self.src = _as_int(src) if src not in (None, b'', '') else 0
        self._text = ''
        self.blocks = []
        self._index = 0
        self._current = None
        self._open_pos = None
        self._sent_voice = False
        self.voice_emb = None
        self.header_seen = False
        self.seen = time.monotonic()

    def set_text(self, text):
        text = text or ''
        if text == self._text:
            return
        self._text = text
        self.blocks = encode_emb_blocks(text) if text else []
        self._index = 0
        self._current = None
        if text:
            _announce(self.src, text)

    def refresh(self):
        if _enabled:
            self.set_text(resolve(self.src))
        elif self._text:
            self.set_text('')

    def emb_for_position(self, pos, voice_emb):
        new_superframe = (
            self._open_pos is None
            or pos < self._open_pos
            or (pos == 0 and self._open_pos != 0)
        )
        if new_superframe:
            self.refresh()
            self._advance(voice_emb)
        self._open_pos = pos
        return self._current or voice_emb

    def fragment(self, frame_type, vseq, voice_emb):
        """Bridge splice: voice bursts B-E. Other frames keep the voice LC."""
        if vseq not in voice_emb:
            raise KeyError(vseq)
        if frame_type != HBPF_VOICE or vseq not in (1, 2, 3, 4):
            return voice_emb[vseq]
        emb = self.emb_for_position(vseq, voice_emb)
        return emb.get(vseq, voice_emb[vseq])

    def repeat_fragment(self, frame_type, vseq, voice_emb):
        """Local repeat keeps the original burst unless this superframe is alias."""
        if frame_type != HBPF_VOICE or vseq not in (1, 2, 3, 4):
            return None
        chosen = self.emb_for_position(vseq, voice_emb)
        if chosen is voice_emb:
            return None
        frag = chosen.get(vseq)
        if frag is None or len(frag) != 32:
            return None
        return frag

    def _advance(self, voice_emb):
        if not _enabled or not self.blocks:
            self._current = voice_emb
            self._sent_voice = True
            return
        cycle = [voice_emb] + self.blocks
        if self._sent_voice and self._index == 0:
            self._index = 1
        self._current = cycle[self._index % len(cycle)]
        self._index += 1
        self._sent_voice = True


def _voice_emb_from_header(payload):
    bits = bitarray(endian='big')
    bits.frombytes(payload[:33])
    if len(bits) < 264:
        return None
    try:
        lc = bptc.decode_full_lc(bits[0:98] + bits[166:264]).tobytes()
    except Exception:
        return None
    if len(lc) < 9:
        return None
    return bptc.encode_emblc(bytes(lc[:9]))


def _trim_repeat():
    if len(_repeat) <= 512:
        return
    for key in list(_repeat)[:256]:
        _repeat.pop(key, None)


def _expire_repeat():
    now = time.monotonic()
    stale = [
        key for key, sched in _repeat.items()
        if now - sched.seen > _REPEAT_IDLE
    ]
    for key in stale:
        _repeat.pop(key, None)


def _bind_repeat_header(stream_id, src, payload):
    """Keep an in-progress cadence when the same subscriber repeats a header.

    A new source starts a new schedule. The decoded header LC is the voice
    superframe identity. If the header cannot be decoded, an empty dict is
    only that identity: repeat leaves those bursts unchanged and never writes
    a guessed group LC.
    """
    src_id = _as_int(src)
    voice = _voice_emb_from_header(payload)
    if voice is None:
        voice = {}
    sched = _repeat.get(stream_id)
    if sched is None or sched.src != src_id:
        sched = AliasSchedule(src)
        _repeat[stream_id] = sched
        _trim_repeat()
    elif sched.voice_emb is not None and sched._current is sched.voice_emb:
        sched._current = voice
    sched.voice_emb = voice
    sched.header_seen = True
    sched.seen = time.monotonic()


def embed_repeat(packet, src, frame_type, vseq, stream_id):
    """Splice talker alias into a locally repeated DMRD frame.

    Voice superframes are left as received. Alias superframes replace only
    the embedded-LC bits, so hotspots on this master see the callsign too.
    Bursts that arrive before the voice header are not rewritten.
    """
    if not _enabled or not packet or len(packet) < 53 or not stream_id:
        return packet
    _expire_repeat()
    payload = packet[20:53]
    try:
        note_embedded(src, frame_type, vseq, payload)
    except Exception:
        logger.debug('Talker Alias ingest failed', exc_info=True)
    if frame_type == HBPF_DATA_SYNC and vseq == HBPF_SLT_VTERM:
        _repeat.pop(stream_id, None)
        return packet
    if frame_type == HBPF_DATA_SYNC and vseq == HBPF_SLT_VHEAD:
        _bind_repeat_header(stream_id, src, payload)
        return packet
    sched = _repeat.get(stream_id)
    if sched is not None:
        sched.seen = time.monotonic()
    if frame_type != HBPF_VOICE or vseq not in (1, 2, 3, 4):
        return packet
    if sched is None or not sched.header_seen or sched.voice_emb is None:
        return packet
    frag = sched.repeat_fragment(frame_type, vseq, sched.voice_emb)
    if frag is None:
        return packet
    bits = bitarray(endian='big')
    bits.frombytes(payload)
    if len(bits) < 148:
        return packet
    bits[116:148] = frag
    return packet[:20] + bits.tobytes() + packet[53:]
