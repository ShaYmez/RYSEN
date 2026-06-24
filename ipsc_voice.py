#!/usr/bin/env python3
###############################################################################
#   IPSC GROUP_VOICE → internal DMRD translation for RYSEN routing
#   Ported from ipsc2hbp translate/translator.py (outbound path only, Phase 1)
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
from dmr_utils3.ambe_utils import convert49BitTo72BitAMBE
from dmr_utils3.const import EMB, SLOT_TYPE, BS_VOICE_SYNC, BS_DATA_SYNC

from const import DMRD, LC_OPT, HBPF_SLT_VHEAD, HBPF_SLT_VTERM
from ipsc_const import (
    VOICE_HEAD, VOICE_TERM,
    TS_CALL_MSK,
    GV_CALL_SEQ_OFF, GV_CALL_INFO_OFF,
    GV_SRC_SUB_OFF, GV_DST_GROUP_OFF,
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
    """Convert IPSC GROUP_VOICE bursts into DMRD packets for routerHBP.dmrd_received()."""

    def __init__(self, ts_prefer_call_info=False):
        self._ts_prefer_call_info = ts_prefer_call_info
        self._out_stream_id = {1: None, 2: None}
        self._out_ipsc_stream_id = {1: None, 2: None}
        self._out_seq = 0
        self._out_frame_pos = {1: 0, 2: 0}
        self._out_lc = {1: None, 2: None}
        self._out_emb_lc = {1: None, 2: None}

    def reset(self):
        for ts in (1, 2):
            self._out_stream_id[ts] = None
            self._out_ipsc_stream_id[ts] = None
            self._out_lc[ts] = None
            self._out_emb_lc[ts] = None
            self._out_frame_pos[ts] = 0
        self._out_seq = 0

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
