#!/usr/bin/env python3
###############################################################################
#   IPSC GROUP_VOICE ↔ internal DMRD translation for RYSEN routing
#   Inbound path ported from ipsc2hbp; outbound encode added in Phase 2c
#
#   Copyright (C) 2026 Shane Daley, M0VUB
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import os
import struct

from bitarray import bitarray

from dmr_utils3 import bptc
from dmr_utils3.ambe_utils import convert49BitTo72BitAMBE, convert72BitTo49BitAMBE
from dmr_utils3.const import EMB, SLOT_TYPE, BS_VOICE_SYNC, BS_DATA_SYNC

from const import DMRD, LC_OPT, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE, HBPF_VOICE_SYNC, HBPF_DATA_SYNC
from ipsc_const import (
    GROUP_VOICE, VOICE_HEAD, VOICE_TERM, SLOT1_VOICE, SLOT2_VOICE,
    TS_CALL_MSK, END_MSK,
    GV_CALL_SEQ_OFF, GV_CALL_INFO_OFF, GV_BURST_TYPE_OFF,
    GV_SRC_SUB_OFF, GV_DST_GROUP_OFF, GV_MIN_LEN, GV_HEAD_LEN, GV_VOICE_LEN,
    DEFAULT_PEER_CALL_TYPE, DEFAULT_PEER_CALL_CTRL,
    HBPF_TGID_TS2,
    HBPF_FRAMETYPE_VOICE, HBPF_FRAMETYPE_VOICESYNC, HBPF_FRAMETYPE_DATASYNC,
)

_NULL_EMB_LC = bitarray(32, endian='big')
_NULL_EMB_LC.setall(0)
_EMB_BURST_NAMES = ('BURST_B', 'BURST_C', 'BURST_D', 'BURST_E', 'BURST_F')


def _ambe49_to_72(ba49):
    raw = convert49BitTo72BitAMBE(ba49)
    out = bitarray(endian='big')
    out.frombytes(bytes(raw))
    return out


def _build_embed(pos, emb_lc):
    if pos == 0:
        return BS_VOICE_SYNC
    name = _EMB_BURST_NAMES[pos - 1]
    lc_bits = emb_lc.get(pos, _NULL_EMB_LC) if emb_lc and pos <= 4 else _NULL_EMB_LC
    return EMB[name][:8] + lc_bits + EMB[name][-8:]


def _decode_lc_from_dmrd(payload_33):
    """Extract the 9-byte LC word from a DMRD voice LC header/terminator payload."""
    frame_bits = bitarray(endian='big')
    frame_bits.frombytes(payload_33)
    if len(frame_bits) < 264:
        frame_bits.extend([0] * (264 - len(frame_bits)))
    bptc_bits = frame_bits[0:98] + frame_bits[166:264]
    return bptc.decode_full_lc(bptc_bits).tobytes()


def _build_ipsc_voice_payload(lc, burst_type):
    """Motorola LC payload for VOICE_HEAD / VOICE_TERM (23 bytes after burst type byte)."""
    if burst_type == VOICE_HEAD:
        fec = bptc.rs129.lc_header_encode(lc[:9])
        type_tag = b'\x11'
    else:
        fec = bptc.rs129.lc_terminator_encode(lc[:9])
        type_tag = b'\x12'
    return (
        b'\x80'
        + struct.pack('>H', 10)
        + b'\x80'
        + b'\x0a'
        + struct.pack('>H', 0x60)
        + lc[:9]
        + fec
        + b'\x00' + type_tag + b'\x00\x00'
    )


def _build_slot_voice_payload(ts, pos, ambe_19, emb_lc, lc):
    """Motorola SLOT_VOICE payload (bytes 30+ in a 52-byte GROUP_VOICE packet)."""
    slot_burst = SLOT2_VOICE if ts == 2 else SLOT1_VOICE
    if pos == 0:
        return bytes([slot_burst]) + b'\x14\x40' + ambe_19
    if pos == 4:
        emb_frag = (
            emb_lc[4].tobytes()
            if emb_lc and 4 in emb_lc
            else _NULL_EMB_LC.tobytes()
        )
        return (
            bytes([slot_burst]) + b'\x22\x16' + ambe_19
            + emb_frag + lc[0:3] + lc[3:6] + lc[6:9] + b'\x14'
        )
    if pos == 5:
        return bytes([slot_burst]) + b'\x19\x06' + ambe_19 + b'\x00\x00\x00\x00\x10'
    emb_frag = (
        emb_lc[pos].tobytes()
        if emb_lc and pos in emb_lc
        else _NULL_EMB_LC.tobytes()
    )
    emb_hdr = EMB[_EMB_BURST_NAMES[pos - 1]][:8].tobytes()[0] & 0xFE
    return bytes([slot_burst]) + b'\x19\x06' + ambe_19 + emb_frag + bytes([emb_hdr])


def _dmrd_frame_position(frame_type, dtype_vseq):
    if frame_type == HBPF_VOICE_SYNC and dtype_vseq == 0:
        return 0
    if dtype_vseq == 4:
        return 4
    if dtype_vseq >= 5:
        return 5
    return max(dtype_vseq, 1)


class IpscVoiceTranslator:
    """Bidirectional IPSC GROUP_VOICE ↔ DMRD for routerHBP / routerIPSC."""

    def __init__(self, master_id=0, ts_prefer_call_info=False):
        self._master_id_b = int(master_id).to_bytes(4, 'big')
        self._peer_call_type = DEFAULT_PEER_CALL_TYPE
        self._peer_call_ctrl = DEFAULT_PEER_CALL_CTRL
        self._ts_prefer_call_info = ts_prefer_call_info
        self._out_stream_id = {1: None, 2: None}
        self._out_ipsc_stream_id = {1: None, 2: None}
        self._out_seq = 0
        self._out_frame_pos = {1: 0, 2: 0}
        self._out_lc = {1: None, 2: None}
        self._out_emb_lc = {1: None, 2: None}
        self._enc_stream_call_id = {}
        self._enc_lc = {}
        self._enc_emb_lc = {}
        self._enc_rtp_seq = {}
        self._enc_rtp_ts = {}
        self._enc_stream_ctr = 0

    def learn_peer_header(self, data):
        """Capture call-type / call-control bytes from an inbound GROUP_VOICE packet."""
        if len(data) >= 17:
            self._peer_call_type = data[12:13]
            self._peer_call_ctrl = data[13:17]

    def reset(self):
        for ts in (1, 2):
            self._out_stream_id[ts] = None
            self._out_ipsc_stream_id[ts] = None
            self._out_lc[ts] = None
            self._out_emb_lc[ts] = None
            self._out_frame_pos[ts] = 0
        self._out_seq = 0
        self._enc_stream_call_id = {}
        self._enc_lc = {}
        self._enc_emb_lc = {}
        self._enc_rtp_seq = {}
        self._enc_rtp_ts = {}

    def translate(self, data, ts, burst_type):
        """
        Return a complete DMRD packet (55 bytes) or None if the burst should be skipped.
        """
        if burst_type not in (VOICE_HEAD, VOICE_TERM):
            ts_ci = 2 if (data[GV_CALL_INFO_OFF] & TS_CALL_MSK) else 1
            if self._ts_prefer_call_info and ts_ci != ts:
                ts = ts_ci

        ipsc_stream_id = data[GV_CALL_SEQ_OFF]
        peer_id_b = data[1:5]
        src_sub = data[GV_SRC_SUB_OFF:GV_SRC_SUB_OFF + 3]
        dst_group = data[GV_DST_GROUP_OFF:GV_DST_GROUP_OFF + 3]
        flags = HBPF_TGID_TS2 if ts == 2 else 0x00

        if burst_type == VOICE_HEAD:
            if (self._out_stream_id[ts] is not None
                    and self._out_ipsc_stream_id[ts] is not None
                    and self._out_ipsc_stream_id[ts] != ipsc_stream_id):
                self._clear_ts(ts)
            if self._out_stream_id[ts] is None:
                self._out_stream_id[ts] = os.urandom(4)
                self._out_ipsc_stream_id[ts] = ipsc_stream_id
            self._out_frame_pos[ts] = 0
            lc = LC_OPT + dst_group + src_sub
            self._out_lc[ts] = lc
            self._out_emb_lc[ts] = bptc.encode_emblc(lc)
            full_lc = bptc.encode_header_lc(lc)
            frame_bits = (
                full_lc[0:98]
                + SLOT_TYPE['VOICE_LC_HEAD'][:10]
                + BS_DATA_SYNC
                + SLOT_TYPE['VOICE_LC_HEAD'][-10:]
                + full_lc[98:]
            )
            payload_33 = frame_bits.tobytes()
            flags |= HBPF_FRAMETYPE_DATASYNC | HBPF_SLT_VHEAD

        elif burst_type == VOICE_TERM:
            if self._out_stream_id[ts] is None:
                return None
            lc = self._out_lc[ts] if self._out_lc[ts] else LC_OPT + dst_group + src_sub
            full_lc = bptc.encode_terminator_lc(lc)
            frame_bits = (
                full_lc[0:98]
                + SLOT_TYPE['VOICE_LC_TERM'][:10]
                + BS_DATA_SYNC
                + SLOT_TYPE['VOICE_LC_TERM'][-10:]
                + full_lc[98:]
            )
            payload_33 = frame_bits.tobytes()
            flags |= HBPF_FRAMETYPE_DATASYNC | HBPF_SLT_VTERM

        else:
            if (self._out_stream_id[ts] is not None
                    and self._out_ipsc_stream_id[ts] is not None
                    and self._out_ipsc_stream_id[ts] != ipsc_stream_id):
                self._clear_ts(ts)
            if self._out_stream_id[ts] is None:
                if len(data) < 33 or data[32] != 0x16:
                    return None
                lc = LC_OPT + dst_group + src_sub
                self._out_stream_id[ts] = os.urandom(4)
                self._out_ipsc_stream_id[ts] = ipsc_stream_id
                self._out_lc[ts] = lc
                self._out_emb_lc[ts] = bptc.encode_emblc(lc)
                self._out_frame_pos[ts] = 4
            if len(data) < 52:
                return None
            raw_ba = bitarray(endian='big')
            raw_ba.frombytes(data[33:52])
            a1_72 = _ambe49_to_72(raw_ba[0:49])
            a2_72 = _ambe49_to_72(raw_ba[50:99])
            a3_72 = _ambe49_to_72(raw_ba[100:149])
            pos = self._out_frame_pos[ts] % 6
            embed = _build_embed(pos, self._out_emb_lc[ts])
            frame_bits = a1_72 + a2_72[:36] + embed + a2_72[36:] + a3_72
            payload_33 = frame_bits.tobytes()
            flags |= HBPF_FRAMETYPE_VOICESYNC if pos == 0 else (HBPF_FRAMETYPE_VOICE | pos)
            self._out_frame_pos[ts] += 1

        dmrd = (
            DMRD
            + bytes([self._out_seq])
            + src_sub
            + dst_group
            + peer_id_b
            + bytes([flags])
            + self._out_stream_id[ts]
            + payload_33
            + b'\x00\x00'
        )
        self._out_seq = (self._out_seq + 1) & 0xFF

        if burst_type == VOICE_TERM:
            self._clear_ts(ts)

        return dmrd

    def _clear_ts(self, ts):
        self._out_stream_id[ts] = None
        self._out_ipsc_stream_id[ts] = None
        self._out_lc[ts] = None
        self._out_emb_lc[ts] = None
        self._out_frame_pos[ts] = 0

    def _build_gv(self, src_sub, dst_group, call_info, rtp_hdr, gv_payload, call_seq):
        return (
            bytes([GROUP_VOICE])
            + self._master_id_b
            + bytes([call_seq])
            + src_sub
            + dst_group
            + self._peer_call_type
            + self._peer_call_ctrl
            + bytes([call_info])
            + rtp_hdr
            + gv_payload
        )

    def _stream_key(self, ts, stream_id):
        return (ts, stream_id)

    def _call_seq_for_stream(self, key, new_call=False):
        if new_call or key not in self._enc_stream_call_id:
            self._enc_stream_ctr = (self._enc_stream_ctr + 1) & 0xFF
            self._enc_stream_call_id[key] = self._enc_stream_ctr
        return self._enc_stream_call_id[key]

    def _next_rtp_hdr(self, key, pt, advance_ts=False):
        seq = self._enc_rtp_seq.get(key, 0)
        ts_val = self._enc_rtp_ts.get(key, 0)
        rtp_hdr = (
            b'\x80' + bytes([pt])
            + struct.pack('>H', seq & 0xFFFF)
            + struct.pack('>I', ts_val)
            + b'\x00\x00\x00\x00'
        )
        self._enc_rtp_seq[key] = (seq + 1) & 0xFFFF
        if advance_ts:
            self._enc_rtp_ts[key] = (ts_val + 480) & 0xFFFFFFFF
        return rtp_hdr

    def _clear_encode_stream(self, key):
        self._enc_stream_call_id.pop(key, None)
        self._enc_lc.pop(key, None)
        self._enc_emb_lc.pop(key, None)
        self._enc_rtp_seq.pop(key, None)
        self._enc_rtp_ts.pop(key, None)

    def encode(self, dmrd):
        """Convert a bridged DMRD packet into one Motorola GROUP_VOICE burst."""
        if len(dmrd) < 53 or dmrd[:4] != DMRD:
            return None

        flags = dmrd[15]
        ts = 2 if (flags & HBPF_TGID_TS2) else 1
        frame_type = (flags & 0x30) >> 4
        dtype_vseq = flags & 0x0F
        stream_id = dmrd[16:20]
        src_sub = dmrd[5:8]
        dst_group = dmrd[8:11]
        payload = dmrd[20:53]
        key = self._stream_key(ts, stream_id)

        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
            lc = _decode_lc_from_dmrd(payload)
            self._enc_lc[key] = lc
            self._enc_emb_lc[key] = bptc.encode_emblc(lc)
            call_seq = self._call_seq_for_stream(key, new_call=True)
            call_info = TS_CALL_MSK if ts == 2 else 0x00
            gv_payload = bytes([VOICE_HEAD]) + _build_ipsc_voice_payload(lc, VOICE_HEAD)
            rtp_hdr = self._next_rtp_hdr(key, 0xdd)
            return self._build_gv(src_sub, dst_group, call_info, rtp_hdr, gv_payload, call_seq)

        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
            lc = self._enc_lc.get(key) or _decode_lc_from_dmrd(payload) or (LC_OPT + dst_group + src_sub)
            call_seq = self._call_seq_for_stream(key)
            call_info = (END_MSK | TS_CALL_MSK) if ts == 2 else END_MSK
            gv_payload = bytes([VOICE_TERM]) + _build_ipsc_voice_payload(lc, VOICE_TERM)
            rtp_hdr = self._next_rtp_hdr(key, 0x5e)
            pkt = self._build_gv(src_sub, dst_group, call_info, rtp_hdr, gv_payload, call_seq)
            self._clear_encode_stream(key)
            return pkt

        if (frame_type == HBPF_VOICE_SYNC and dtype_vseq == 0) or (
                frame_type == HBPF_VOICE and dtype_vseq in (1, 2, 3, 4, 5)):
            lc = self._enc_lc.get(key)
            if lc is None:
                lc = LC_OPT + dst_group + src_sub
                self._enc_lc[key] = lc
                self._enc_emb_lc[key] = bptc.encode_emblc(lc)
                self._call_seq_for_stream(key, new_call=True)
            emb_lc = self._enc_emb_lc.get(key)
            pos = _dmrd_frame_position(frame_type, dtype_vseq)
            ambe_19 = self._dmrd_payload_to_ambe(payload)
            gv_payload = _build_slot_voice_payload(ts, pos, ambe_19, emb_lc, lc)
            call_seq = self._call_seq_for_stream(key)
            call_info = 0x00
            rtp_hdr = self._next_rtp_hdr(key, 0x5d, advance_ts=True)
            return self._build_gv(src_sub, dst_group, call_info, rtp_hdr, gv_payload, call_seq)

        return None

    def _dmrd_payload_to_ambe(self, payload):
        frame_bits = bitarray(endian='big')
        frame_bits.frombytes(payload)
        if len(frame_bits) < 248:
            frame_bits.extend([0] * (248 - len(frame_bits)))
        a1_72 = frame_bits[0:72]
        a2_72 = frame_bits[72:108] + frame_bits[140:176]
        a3_72 = frame_bits[176:248]
        packed = bitarray(endian='big')
        packed.extend(convert72BitTo49BitAMBE(a1_72))
        packed.append(0)
        packed.extend(convert72BitTo49BitAMBE(a2_72))
        packed.append(0)
        packed.extend(convert72BitTo49BitAMBE(a3_72))
        while len(packed) < 152:
            packed.append(0)
        return packed.tobytes()[:19]
