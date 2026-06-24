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
    GV_SRC_SUB_OFF, GV_DST_GROUP_OFF, GV_MIN_LEN,
    HBPF_TGID_TS2, HBPF_FRAMETYPE_VOICE, HBPF_FRAMETYPE_VOICESYNC, HBPF_FRAMETYPE_DATASYNC,
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


class IpscVoiceTranslator:
    """Bidirectional IPSC GROUP_VOICE ↔ DMRD for routerHBP / routerIPSC."""

    def __init__(self, ts_prefer_call_info=False):
        self._ts_prefer_call_info = ts_prefer_call_info
        self._out_stream_id = {1: None, 2: None}
        self._out_ipsc_stream_id = {1: None, 2: None}
        self._out_seq = 0
        self._out_frame_pos = {1: 0, 2: 0}
        self._out_lc = {1: None, 2: None}
        self._out_emb_lc = {1: None, 2: None}
        self._enc_stream_seq = {}

    def reset(self):
        for ts in (1, 2):
            self._out_stream_id[ts] = None
            self._out_ipsc_stream_id[ts] = None
            self._out_lc[ts] = None
            self._out_emb_lc[ts] = None
            self._out_frame_pos[ts] = 0
        self._out_seq = 0
        self._enc_stream_seq = {}

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

    def encode(self, dmrd):
        """Convert a bridged DMRD packet into one GROUP_VOICE burst (Phase 2c outbound)."""
        if len(dmrd) < 53 or dmrd[:4] != DMRD:
            return None

        flags = dmrd[15]
        ts = 2 if (flags & HBPF_TGID_TS2) else 1
        frame_type = (flags & 0x30) >> 4
        dtype_vseq = flags & 0x0F
        stream_id = dmrd[16:20]
        peer_id = dmrd[11:15]
        src_sub = dmrd[5:8]
        dst_group = dmrd[8:11]
        payload = dmrd[20:53]
        call_seq = self._encode_call_seq(ts, stream_id)
        voice_extra = b''

        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
            burst_type = VOICE_HEAD
            call_info = TS_CALL_MSK if ts == 2 else 0x00
            pkt_len = GV_MIN_LEN
        elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
            burst_type = VOICE_TERM
            call_info = (END_MSK | TS_CALL_MSK) if ts == 2 else END_MSK
            pkt_len = GV_MIN_LEN
            self._clear_encode_stream(ts, stream_id)
        elif frame_type == HBPF_VOICE_SYNC and dtype_vseq == 0:
            burst_type = SLOT2_VOICE if ts == 2 else SLOT1_VOICE
            call_info = 0x00
            voice_extra = self._dmrd_payload_to_ambe(payload)
            pkt_len = 52
        elif frame_type == HBPF_VOICE and dtype_vseq in (1, 2, 3, 4):
            burst_type = SLOT2_VOICE if ts == 2 else SLOT1_VOICE
            call_info = 0x00
            voice_extra = self._dmrd_payload_to_ambe(payload)
            pkt_len = 52
        else:
            return None

        pkt = bytearray(pkt_len)
        pkt[0] = GROUP_VOICE
        pkt[1:5] = peer_id
        pkt[5] = call_seq
        pkt[6:9] = src_sub
        pkt[9:12] = dst_group
        pkt[17] = call_info
        pkt[GV_BURST_TYPE_OFF] = burst_type
        if voice_extra:
            pkt[33:33 + len(voice_extra)] = voice_extra
        return bytes(pkt)

    def _encode_call_seq(self, ts, stream_id):
        key = (ts, stream_id)
        if key not in self._enc_stream_seq:
            self._enc_stream_seq[key] = stream_id[0] or 1
        return self._enc_stream_seq[key]

    def _clear_encode_stream(self, ts, stream_id):
        self._enc_stream_seq.pop((ts, stream_id), None)

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
