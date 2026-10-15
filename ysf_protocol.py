#!/usr/bin/env python3
###############################################################################
#   YSF reflector network frames and V/D mode 2 (DN) voice packing.
#
#   Wire layout follows the public YSFD header (YSFNetwork) and the Yaesu
#   amateur digital specification, revision 1.02: FICH figure 4-26, V/D mode 2
#   DCH figure 4-31, and VCH/VeCH figure 4-32. This is not a copy of MMDVM.
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

'''YSF reflector client framing and DN (V/D mode 2) AMBE extract/insert.'''

# 40-bit frame sync, then 200-bit FICH, then five (40-bit DCH + 104-bit voice) blocks.
FRAME_SYNC = bytes.fromhex('D471C9634D')
FRAME_BYTES = 120
YSFD_BYTES = 155
CALLSIGN_LENGTH = 10

# One DN transmission cycles FN 0..6 with FT = 6. pYSF stores FN 4 as the
# 10-character remark, FN 5 as station id + 5-character serial, and FN 6 as
# the radio-id block. The Radio column is that block's model byte (0x2B is
# FT-70D) unless the collector maps the serial, for example
# ser_lnks = {"SYSTEMX": "SYSX1"}.
CSD_COUNT = 7
RADIO_TEXT = b'SYSTEMX'
RADIO_SERIAL = b'SYSX1'
RADIO_CODE = 0x01

FI_HEADER = 0
FI_COMMUNICATION = 1
FI_TERMINATOR = 2
DT_VD_MODE1 = 0
DT_DATA_FR = 1
DT_VD_MODE2 = 2
DT_VOICE_FR = 3

# Spec figure 4-9 / the decoded FICH bit order used on the air (MSB first).
_FICH_FI = (0, 2)
_FICH_CS = (2, 2)
_FICH_CM = (4, 2)
_FICH_BN = (6, 2)
_FICH_BT = (8, 2)
_FICH_FN = (10, 3)
_FICH_FT = (13, 3)
_FICH_MR = (18, 3)
_FICH_VOIP = (21, 1)
_FICH_DT = (22, 2)
_FICH_SQL = (24, 1)
_FICH_SC = (25, 7)

# Extended Golay (24, 12) rows from spec appendix A, top row is d0.
_GOLAY = (
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1),
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 0),
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1),
    (0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 1, 1, 0),
    (0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 1, 1),
    (0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1),
    (0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1),
    (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 1, 0),
    (0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 1, 0, 0),
    (0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 1, 0, 0, 0),
    (0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 1),
    (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1),
)

# V/D mode 2 voice interleave: block length 26, depth 4. Air index -> data index.
_VD2_INTERLEAVE = tuple(i + (j * 26) for i in range(26) for j in range(4))


def pad_callsign(text):
    """10-character YSF callsign, space padded, ASCII only."""
    if isinstance(text, bytes):
        text = text.decode('ascii', 'ignore')
    raw = str(text or '').upper().encode('ascii', 'replace')
    cleaned = bytes(b if 32 <= b < 127 else 0x20 for b in raw)
    return cleaned[:CALLSIGN_LENGTH].ljust(CALLSIGN_LENGTH)


def bytes_to_bits(data):
    bits = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits


def bits_to_bytes(bits):
    if len(bits) % 8:
        raise ValueError('bit length %s is not a whole number of bytes' % len(bits))
    out = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit in bits[start:start + 8]:
            value = (value << 1) | (bit & 1)
        out.append(value)
    return bytes(out)


def _put_field(bits, start, width, value):
    for offset in range(width):
        bits[start + offset] = (int(value) >> (width - 1 - offset)) & 1


def _get_field(bits, start, width):
    value = 0
    for offset in range(width):
        value = (value << 1) | (bits[start + offset] & 1)
    return value


def pn_bits(count):
    """PN(9, 5) whitening sequence. Spec figure 4-28, reset for each unit.

    S08..S00 starts at 111001001. The output is S00. Feedback is S00 xor S04
    and is shifted in at S08.
    """
    reg = [1, 1, 1, 0, 0, 1, 0, 0, 1]
    out = []
    for _ in range(count):
        out.append(reg[8])
        feedback = reg[4] ^ reg[8]
        reg = [feedback] + reg[:8]
    return out


def crc16(bits):
    """CRC-16, poly 0x1021, registers start at 0, result inverted. Spec 4.5."""
    crc = 0
    for bit in bits:
        feedback = ((crc >> 15) & 1) ^ (bit & 1)
        crc = (crc << 1) & 0xFFFF
        if feedback:
            crc ^= 0x1021
    crc ^= 0xFFFF
    return [((crc >> shift) & 1) for shift in range(15, -1, -1)]


def golay_encode(data12):
    """Systematic extended Golay. data12[0] is d11."""
    if len(data12) != 12:
        raise ValueError('Golay data must be 12 bits')
    acc = [0] * 24
    for index, bit in enumerate(data12):
        if bit & 1:
            row = _GOLAY[11 - index]
            for column, value in enumerate(row):
                acc[column] ^= value
    return acc


def golay_data(codeword):
    """Return the 12 data bits when the codeword matches a fresh encode."""
    data = codeword[:12]
    if golay_encode(data) != list(codeword):
        return None
    return data


def _conv_step(memory, bit):
    """One K=5 step. memory is the previous 4 inputs, bit 0 the newest."""
    state = ((memory & 0xF) << 1) | (bit & 1)
    def tap(shift):
        return (state >> shift) & 1
    # G1 = x^4 + x^3 + 1, G2 = x^4 + x^2 + x + 1. Spec appendix B.
    g1 = tap(0) ^ tap(3) ^ tap(4)
    g2 = tap(0) ^ tap(1) ^ tap(2) ^ tap(4)
    return state & 0xF, g1, g2


def conv_encode(bits):
    """Rate 1/2 K=5 code with four zero tail bits. Output is G1, G2, ..."""
    memory = 0
    out = []
    for bit in list(bits) + [0, 0, 0, 0]:
        memory, g1, g2 = _conv_step(memory, bit)
        out.append(g1)
        out.append(g2)
    return out


def conv_decode(coded):
    """Viterbi decode. ``coded`` includes the tail pairs. Tail bits are dropped."""
    if len(coded) % 2:
        raise ValueError('convolutional output must be even')
    steps = len(coded) // 2
    inf = 10 ** 9
    cost = [inf] * 16
    cost[0] = 0
    history = []
    for step in range(steps):
        g1 = coded[step * 2] & 1
        g2 = coded[step * 2 + 1] & 1
        next_cost = [inf] * 16
        choice = [None] * 16
        for state in range(16):
            if cost[state] >= inf:
                continue
            for bit in (0, 1):
                new_state, eg1, eg2 = _conv_step(state, bit)
                branch = cost[state] + (eg1 != g1) + (eg2 != g2)
                if branch < next_cost[new_state]:
                    next_cost[new_state] = branch
                    choice[new_state] = (state, bit)
        cost = next_cost
        history.append(choice)
    state = min(range(16), key=lambda item: cost[item])
    decoded = []
    for choice in reversed(history):
        if choice[state] is None:
            return None
        state, bit = choice[state]
        decoded.append(bit)
    decoded.reverse()
    if len(decoded) < 4:
        return None
    return decoded[:-4]


def _bits_to_dibits(bits):
    if len(bits) % 2:
        raise ValueError('dibits need an even number of bits')
    return [((bits[i] & 1) << 1) | (bits[i + 1] & 1) for i in range(0, len(bits), 2)]


def _dibits_to_bits(dibits):
    bits = []
    for dibit in dibits:
        bits.append((dibit >> 1) & 1)
        bits.append(dibit & 1)
    return bits


def interleave_dibits(dibits, width, depth):
    """Write ``width`` dibits per row, ``depth`` rows, read down the columns."""
    if len(dibits) != width * depth:
        raise ValueError('dibit interleave length')
    out = [0] * (width * depth)
    for col in range(width):
        for row in range(depth):
            out[col * depth + row] = dibits[row * width + col]
    return out


def deinterleave_dibits(air, width, depth):
    if len(air) != width * depth:
        raise ValueError('dibit deinterleave length')
    dibits = [0] * (width * depth)
    for col in range(width):
        for row in range(depth):
            dibits[row * width + col] = air[col * depth + row]
    return dibits


def pack_fich(fi, fn, ft, dgid, dt=DT_VD_MODE2):
    """32 FICH information bits for one DN frame."""
    bits = [0] * 32
    _put_field(bits, *_FICH_FI, fi & 3)
    _put_field(bits, *_FICH_CS, 2)  # callsigns assigned
    _put_field(bits, *_FICH_CM, 0)  # group / CQ
    _put_field(bits, *_FICH_FN, fn & 7)
    _put_field(bits, *_FICH_FT, ft & 7)
    _put_field(bits, *_FICH_MR, 1)  # downlink, uplink free
    _put_field(bits, *_FICH_VOIP, 1)
    _put_field(bits, *_FICH_DT, dt & 3)
    if dgid:
        _put_field(bits, *_FICH_SQL, 0)  # squelch code enabled
        _put_field(bits, *_FICH_SC, int(dgid) & 0x7F)
    else:
        _put_field(bits, *_FICH_SQL, 1)  # squelch code disabled
    return bits


def unpack_fich(bits):
    if bits is None or len(bits) < 32:
        return None
    return {
        'fi': _get_field(bits, *_FICH_FI),
        'cs': _get_field(bits, *_FICH_CS),
        'cm': _get_field(bits, *_FICH_CM),
        'bn': _get_field(bits, *_FICH_BN),
        'bt': _get_field(bits, *_FICH_BT),
        'fn': _get_field(bits, *_FICH_FN),
        'ft': _get_field(bits, *_FICH_FT),
        'mr': _get_field(bits, *_FICH_MR),
        'voip': _get_field(bits, *_FICH_VOIP),
        'dt': _get_field(bits, *_FICH_DT),
        'sql': _get_field(bits, *_FICH_SQL),
        'dgid': _get_field(bits, *_FICH_SC),
    }


def _fec_air(info_bits, golay):
    """CRC, optional Golay, tail, convolutional code, dibit interleave."""
    payload = list(info_bits) + crc16(info_bits)
    if golay:
        coded = []
        if len(payload) % 12:
            raise ValueError('Golay input must be a multiple of 12 bits')
        for start in range(0, len(payload), 12):
            coded.extend(golay_encode(payload[start:start + 12]))
        payload = coded
    convolved = conv_encode(payload)
    dibits = _bits_to_dibits(convolved)
    # FICH and V/D mode 2 DCH both interleave 100 dibits as 5 by 20.
    air = interleave_dibits(dibits, 5, 20)
    return _dibits_to_bits(air)


def _fec_info(air_bits, info_len, golay):
    dibits = deinterleave_dibits(_bits_to_dibits(air_bits), 5, 20)
    decoded = conv_decode(_dibits_to_bits(dibits))
    if decoded is None:
        return None
    if golay:
        data = []
        if len(decoded) % 24:
            return None
        for start in range(0, len(decoded), 24):
            word = golay_data(decoded[start:start + 24])
            if word is None:
                return None
            data.extend(word)
        decoded = data
    if len(decoded) < info_len + 16:
        return None
    info = decoded[:info_len]
    if decoded[info_len:info_len + 16] != crc16(info):
        return None
    return info


def encode_fich(info32):
    return _fec_air(info32, golay=True)


def decode_fich(air200):
    return unpack_fich(_fec_info(air200, 32, golay=True))


def encode_dch(info80):
    """V/D mode 2 DCH: whiten the 10 data bytes, then CRC, convolution, interleave.

    The PN sequence covers the callsign bytes before the CRC. The
    convolutional codeword is not whitened again.
    """
    if len(info80) != 80:
        raise ValueError('DCH data is 80 bits')
    whitened = [bit ^ mask for bit, mask in zip(info80, pn_bits(80))]
    payload = whitened + crc16(whitened)
    convolved = conv_encode(payload)
    air = interleave_dibits(_bits_to_dibits(convolved), 5, 20)
    return _dibits_to_bits(air)


def decode_dch(air200):
    dibits = deinterleave_dibits(_bits_to_dibits(air200), 5, 20)
    decoded = conv_decode(_dibits_to_bits(dibits))
    if decoded is None or len(decoded) < 96:
        return None
    info = decoded[:80]
    if decoded[80:96] != crc16(info):
        return None
    return [bit ^ mask for bit, mask in zip(info, pn_bits(80))]


def encode_voice(ambe49):
    """49 AMBE parameter bits -> 104 air bits (majority, whiten, interleave)."""
    if len(ambe49) != 49:
        raise ValueError('DN voice slot is 49 bits')
    raw = []
    for index in range(27):
        bit = ambe49[index] & 1
        raw.extend((bit, bit, bit))
    raw.extend((bit & 1) for bit in ambe49[27:49])
    raw.append(0)
    whitened = [bit ^ mask for bit, mask in zip(raw, pn_bits(104))]
    return [whitened[_VD2_INTERLEAVE[index]] for index in range(104)]


def decode_voice(air104):
    """104 air bits -> 49 AMBE parameter bits, or None when the fixed bit is set."""
    if len(air104) != 104:
        raise ValueError('DN voice air slot is 104 bits')
    whitened = [0] * 104
    for index, bit in enumerate(air104):
        whitened[_VD2_INTERLEAVE[index]] = bit & 1
    raw = [bit ^ mask for bit, mask in zip(whitened, pn_bits(104))]
    ambe = []
    for index in range(0, 81, 3):
        votes = (raw[index] << 2) | (raw[index + 1] << 1) | raw[index + 2]
        ambe.append((0, 0, 0, 1, 0, 1, 1, 1)[votes])
    ambe.extend(raw[81:103])
    if raw[103] != 0:
        return None
    return ambe


def radio_info_block(code=RADIO_CODE):
    """10-byte radio-id block. pYSF takes the model from byte 4.

    Byte 5 is the 0x03 marker and byte 6 is the sum of the bytes before it.
    ``code`` is not a Yaesu model, so an unmapped dashboard prints the number
    instead of a handheld name such as FT-70D.
    """
    block = bytearray(10)
    block[0] = 0x07
    block[1] = 0x22
    block[2] = 0x61
    block[3] = 0x5F
    block[4] = code & 0xFF
    block[5] = 0x03
    block[6] = sum(block[:6]) & 0xFF
    return bytes(block)


def _dch_for_fn(fn, source, gateway):
    source = pad_callsign(source)
    gateway = pad_callsign(gateway)
    cycle = (
        pad_callsign('ALL'),
        source,
        gateway,
        gateway,
        pad_callsign(RADIO_TEXT),
        gateway[:5] + RADIO_SERIAL,
        radio_info_block(),
    )
    return bytes_to_bits(cycle[fn % CSD_COUNT])


def encode_header_csd(info20):
    """20 callsign bytes -> 45 air bytes of one header CSD block.

    Same whitening, CRC and K=5 code as the DN DCH. The interleave is 9 by 20
    because a header block is 20 bytes plus CRC, not the 10-byte DN slot.
    """
    if len(info20) != 20:
        raise ValueError('header CSD is 20 bytes')
    info = bytes_to_bits(info20)
    whitened = [bit ^ mask for bit, mask in zip(info, pn_bits(160))]
    coded = conv_encode(whitened + crc16(whitened))
    air = interleave_dibits(_bits_to_dibits(coded), 9, 20)
    return bits_to_bytes(_dibits_to_bits(air))


def decode_header_csd(air45):
    """45 header-CSD air bytes -> 20 callsign bytes, or None when the CRC fails."""
    if len(air45) != 45:
        return None
    dibits = deinterleave_dibits(_bits_to_dibits(bytes_to_bits(air45)), 9, 20)
    decoded = conv_decode(_dibits_to_bits(dibits))
    if decoded is None or len(decoded) < 176:
        return None
    info = decoded[:160]
    if decoded[160:176] != crc16(info):
        return None
    raw = [bit ^ mask for bit, mask in zip(info, pn_bits(160))]
    return bits_to_bytes(raw)


def encode_header_frame(dest, source, downlink, uplink, dgid, ft=None):
    """120-byte FI=header frame. pYSF opens a stream only when it sees one."""
    if ft is None:
        ft = CSD_COUNT - 1
    fich = encode_fich(pack_fich(FI_HEADER, 0, ft, dgid))
    csd1 = encode_header_csd(pad_callsign(dest) + pad_callsign(source))
    csd2 = encode_header_csd(pad_callsign(downlink) + pad_callsign(uplink))
    body = bytearray()
    for index in range(5):
        start = index * 9
        body += csd1[start:start + 9]
        body += csd2[start:start + 9]
    frame = FRAME_SYNC + bits_to_bytes(fich) + bytes(body)
    if len(frame) != FRAME_BYTES:
        raise ValueError('header frame is %s bytes' % len(frame))
    return frame


def decode_header_frame(frame):
    """Return fich plus the two 20-byte header blocks, or None."""
    if len(frame) != FRAME_BYTES:
        return None
    bits = bytes_to_bits(frame)
    if bits_to_bytes(bits[:40]) != FRAME_SYNC:
        return None
    fich = decode_fich(bits[40:240])
    if fich is None:
        return None
    raw = frame[30:]
    csd1 = bytearray()
    csd2 = bytearray()
    for index in range(5):
        start = index * 18
        csd1 += raw[start:start + 9]
        csd2 += raw[start + 9:start + 18]
    return {
        'fich': fich,
        'csd1': decode_header_csd(bytes(csd1)),
        'csd2': decode_header_csd(bytes(csd2)),
    }


def encode_dn_frame(ambe49s, fn, dgid, source, gateway, fi=FI_COMMUNICATION, ft=None):
    """One 120-byte DN communication frame carrying five 49-bit AMBE words."""
    if len(ambe49s) != 5:
        raise ValueError('a DN frame carries five AMBE words')
    if ft is None:
        ft = CSD_COUNT - 1
    fn = fn % CSD_COUNT
    fich = encode_fich(pack_fich(fi, fn, ft, dgid))
    dch = encode_dch(_dch_for_fn(fn, source, gateway))
    bits = bytes_to_bits(FRAME_SYNC) + fich
    for index, ambe in enumerate(ambe49s):
        bits.extend(dch[index * 40:(index + 1) * 40])
        bits.extend(encode_voice(ambe))
    if len(bits) != FRAME_BYTES * 8:
        raise ValueError('DN frame is %s bits' % len(bits))
    return bits_to_bytes(bits)


def decode_dn_frame(frame):
    """Return fich, five AMBE words, and DCH bits. AMBE is None unless DT is DN."""
    if len(frame) != FRAME_BYTES:
        return None
    bits = bytes_to_bits(frame)
    if bits_to_bytes(bits[:40]) != FRAME_SYNC:
        return None
    fich = decode_fich(bits[40:240])
    if fich is None:
        return None
    dch_air = []
    voice_air = []
    cursor = 240
    for _ in range(5):
        dch_air.extend(bits[cursor:cursor + 40])
        cursor += 40
        voice_air.append(bits[cursor:cursor + 104])
        cursor += 104
    ambes = None
    if fich['dt'] == DT_VD_MODE2 and fich['fi'] == FI_COMMUNICATION:
        ambes = []
        for slot in voice_air:
            word = decode_voice(slot)
            if word is None:
                ambes = None
                break
            ambes.append(word)
    return {
        'fich': fich,
        'ambes': ambes,
        'dch': decode_dch(dch_air),
    }


def pack_poll(callsign):
    return b'YSFP' + pad_callsign(callsign)


def pack_unlink(callsign):
    return b'YSFU' + pad_callsign(callsign)


def pack_ysfd(gateway, source, destination, seq, end, frame):
    if len(frame) != FRAME_BYTES:
        raise ValueError('YSFD payload is 120 bytes')
    status = ((int(seq) & 0x7F) << 1) | (1 if end else 0)
    return b''.join((
        b'YSFD',
        pad_callsign(gateway),
        pad_callsign(source),
        pad_callsign(destination),
        bytes((status,)),
        frame,
    ))


def parse_ysfd(packet):
    """Parse a 155-byte YSFD packet. Other tags return None."""
    if not packet or len(packet) < 4 or packet[:4] != b'YSFD':
        return None
    if len(packet) < YSFD_BYTES:
        return None
    status = packet[34]
    return {
        'gateway': packet[4:14],
        'source': packet[14:24],
        'destination': packet[24:34],
        'end': bool(status & 0x01),
        'seq': (status >> 1) & 0x7F,
        'frame': packet[35:155],
    }
