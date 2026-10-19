#!/usr/bin/env python3
###############################################################################
#   NXDNReflector client framing.
#
#   One poll is 17 bytes and one voice datagram is 43. The 33-byte payload a
#   gateway hands to a radio is a LICH byte, a 4-byte SACCH, and two 14-byte
#   halves. A voice half holds two 49-bit AMBE+2 words at bit offsets 0 and
#   49, each word in 12 + 12 + 25 order. A header or trailer half is the
#   layer-3 call message, which is what the far gateway keys and unkeys on.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''NXDNReflector poll, unlink, and 43-byte voice datagrams.'''

CALLSIGN_LENGTH = 10
POLL_BYTES = 17
DATA_BYTES = 43
PAYLOAD_BYTES = 33
HALF_BYTES = 14

TAG_POLL = b'NXDNP'
TAG_UNLINK = b'NXDNU'
TAG_DATA = b'NXDND'

# NXDND flag byte. Bit 0 is a group call. Bit 3 is end of stream.
# Bit 2 marks the voice header, the bit a gateway sets when the layer-3
# message is a voice call.
FLAG_GROUP = 0x01
FLAG_DATA = 0x02
FLAG_HEADER = 0x04
FLAG_END = 0x08

# LICH: RFCT in bits 7-6, function in 5-4, steal in 3-2, direction in 1,
# parity in 0. Parity is set when the high nibble is 0x80 or 0xB0.
LICH_RFCT_RDCH = 2
LICH_FCT_SACCH_NS = 0
LICH_FCT_UDCH = 1
LICH_FCT_SACCH_SS = 2
LICH_STEAL_FACCH = 0
LICH_STEAL_FACCH1_1 = 1
LICH_STEAL_FACCH1_2 = 2
LICH_STEAL_NONE = 3
LICH_DIRECTION_INBOUND = 0

MESSAGE_VCALL = 0x01
MESSAGE_TX_REL = 0x08

# Superframe SACCH structure codes, in the order the four fragments are sent.
SACCH_STRUCTURES = (3, 2, 1, 0)


def pad_callsign(value):
    """10-character upper-case callsign, space padded."""
    if isinstance(value, bytes):
        text = value.decode('ascii', 'ignore')
    else:
        text = str(value or '')
    return text.strip().upper().encode('ascii', 'replace')[:CALLSIGN_LENGTH].ljust(CALLSIGN_LENGTH)


def _u16(value):
    return int(value or 0) & 0xFFFF


def pack_poll(callsign, talkgroup):
    """17-byte NXDNP: tag, callsign, reflector talkgroup."""
    return TAG_POLL + pad_callsign(callsign) + _u16(talkgroup).to_bytes(2, 'big')


def pack_unlink(callsign, talkgroup):
    """17-byte NXDNU. Same layout as a poll."""
    return TAG_UNLINK + pad_callsign(callsign) + _u16(talkgroup).to_bytes(2, 'big')


def pack_data(src, dst, flags, payload):
    """43-byte NXDND."""
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError('NXDN payload is 33 bytes')
    return b''.join((
        TAG_DATA,
        _u16(src).to_bytes(2, 'big'),
        _u16(dst).to_bytes(2, 'big'),
        bytes((int(flags) & 0xFF,)),
        bytes(payload),
    ))


def parse_data(packet):
    """An NXDND datagram, or None when the packet is not one."""
    if not packet or len(packet) != DATA_BYTES or packet[:5] != TAG_DATA:
        return None
    return {
        'src': int.from_bytes(packet[5:7], 'big'),
        'dst': int.from_bytes(packet[7:9], 'big'),
        'flags': packet[9],
        'payload': packet[10:43],
        'group': bool(packet[9] & FLAG_GROUP),
        'end': bool(packet[9] & FLAG_END),
        'data': bool(packet[9] & FLAG_DATA),
    }


def _read_bit(data, index):
    return 1 if data[index >> 3] & (0x80 >> (index & 7)) else 0


def _write_bit(data, index, bit):
    mask = 0x80 >> (index & 7)
    if bit:
        data[index >> 3] |= mask
    else:
        data[index >> 3] &= (~mask) & 0xFF


def crc6(data, nbits):
    """CRC-6 over the first ``nbits`` bits. Init 0x3F, polynomial 0x27."""
    crc = 0x3F
    for index in range(nbits):
        bit1 = _read_bit(data, index)
        bit2 = 1 if (crc & 0x20) else 0
        crc = (crc << 1) & 0xFF
        if bit1 ^ bit2:
            crc ^= 0x27
    return crc & 0x3F


def _apply_crc6(data, nbits):
    crc = crc6(data, nbits)
    for index in range(6):
        _write_bit(data, nbits + index, (crc >> (5 - index)) & 1)


def lich_byte(rfct, fct, option, direction=LICH_DIRECTION_INBOUND):
    """One raw LICH byte, with the parity bit filled in."""
    value = (
        ((rfct & 3) << 6)
        | ((fct & 3) << 4)
        | ((option & 3) << 2)
        | ((direction & 1) << 1)
    )
    if (value & 0xF0) in (0x80, 0xB0):
        value |= 0x01
    return value


LICH_HEADER = lich_byte(LICH_RFCT_RDCH, LICH_FCT_SACCH_NS, LICH_STEAL_FACCH)
LICH_VOICE = lich_byte(LICH_RFCT_RDCH, LICH_FCT_SACCH_SS, LICH_STEAL_NONE)


def encode_sacch(ran, structure, fragment):
    """4-byte SACCH. ``fragment`` is 18 bits of layer-3, MSB first."""
    raw = bytearray(4)
    raw[0] = ((int(structure) & 3) << 6) | (int(ran or 0) & 0x3F)
    for index, bit in enumerate(list(fragment)[:18]):
        _write_bit(raw, 8 + index, int(bit))
    _apply_crc6(raw, 26)
    return bytes(raw)


def layer3_message(message, src, dst, group=True):
    """14-byte layer-3 body. A group call leaves bit 7 of byte 2 clear."""
    raw = bytearray(14)
    raw[0] = int(message) & 0x3F
    raw[2] = 0x00 if group else 0x80
    ident = _u16(src)
    target = _u16(dst)
    raw[3] = (ident >> 8) & 0xFF
    raw[4] = ident & 0xFF
    raw[5] = (target >> 8) & 0xFF
    raw[6] = target & 0xFF
    return bytes(raw)


def _layer3_bits(src, dst):
    message = layer3_message(MESSAGE_VCALL, src, dst)[:9]
    bits = []
    for byte in message:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits


def sacch_fragment(src, dst, part):
    """18 bits of the voice-call layer-3, and the structure code for ``part``."""
    bits = _layer3_bits(src, dst)
    index = int(part) % 4
    return bits[index * 18:(index + 1) * 18], SACCH_STRUCTURES[index]


def signalling_payload(message, src, dst, ran=0):
    """33 bytes: LICH, idle SACCH, and the call message twice."""
    body = layer3_message(message, src, dst)
    sacch = encode_sacch(ran, 0, [0] * 18)
    return bytes((LICH_HEADER,)) + sacch + body + body


def pack_half(first, second):
    """14 bytes holding two 49-bit words at bit offsets 0 and 49."""
    raw = bytearray(HALF_BYTES)
    for offset, word in ((0, first), (49, second)):
        for index, bit in enumerate(list(word)[:49]):
            _write_bit(raw, offset + index, int(bit))
    return bytes(raw)


def unpack_half(blob):
    """Two 49-bit words from a 14-byte half."""
    words = []
    for offset in (0, 49):
        word = []
        for index in range(49):
            word.append(_read_bit(blob, offset + index))
        words.append(word)
    return words


def voice_payload(words, src, dst, part, ran=0):
    """33-byte voice body. ``words`` is four 49-bit AMBE words."""
    padded = list(words)[:4]
    while len(padded) < 4:
        padded.append([0] * 49)
    fragment, structure = sacch_fragment(src, dst, part)
    sacch = encode_sacch(ran, structure, fragment)
    return bytes((LICH_VOICE,)) + sacch + pack_half(padded[0], padded[1]) + pack_half(padded[2], padded[3])


def is_data(payload, flags=0):
    """A data call. The LICH high nibble is 0x90, or the data flag is set."""
    if flags & FLAG_DATA:
        return True
    if not payload:
        return False
    return (payload[0] & 0xF0) == 0x90


def signalling_kind(payload):
    """'header', 'trailer', or None. Header LICH is 0x81 or 0x83."""
    if not payload or len(payload) < 6 or payload[0] not in (0x81, 0x83):
        return None
    if payload[5] == MESSAGE_VCALL:
        return 'header'
    if payload[5] == MESSAGE_TX_REL:
        return 'trailer'
    return None


def audio_words(payload):
    """Up to four 49-bit words from a voice body, or None when it is not voice."""
    if not payload or len(payload) < PAYLOAD_BYTES:
        return None
    lich = payload[0]
    if ((lich >> 4) & 3) != LICH_FCT_SACCH_SS:
        return None
    option = (lich >> 2) & 3
    first = unpack_half(payload[5:19])
    second = unpack_half(payload[19:33])
    if option == LICH_STEAL_NONE:
        return first + second
    if option == LICH_STEAL_FACCH1_1:
        return second
    if option == LICH_STEAL_FACCH1_2:
        return first
    return None
