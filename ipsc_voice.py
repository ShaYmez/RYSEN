#!/usr/bin/env python3
###############################################################################
#   IPSC GROUP_VOICE / PRIVATE_VOICE ↔ internal DMRD translation for RYSEN routing
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
from twisted.internet import reactor

from dmr_utils3 import bptc
from dmr_utils3.ambe_utils import convert49BitTo72BitAMBE, convert72BitTo49BitAMBE
from dmr_utils3.const import EMB, SLOT_TYPE, BS_VOICE_SYNC, BS_DATA_SYNC

from const import DMRD, LC_OPT, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE, HBPF_VOICE_SYNC, HBPF_DATA_SYNC
from ipsc_const import (
    GROUP_VOICE, PRIVATE_VOICE, VOICE_HEAD, VOICE_TERM, SLOT1_VOICE, SLOT2_VOICE,
    TS_CALL_MSK, END_MSK,
    GV_CALL_SEQ_OFF, GV_CALL_INFO_OFF, GV_BURST_TYPE_OFF,
    GV_SRC_SUB_OFF, GV_DST_GROUP_OFF, GV_MIN_LEN, GV_HEAD_LEN, GV_VOICE_LEN,
    DEFAULT_PEER_CALL_TYPE, DEFAULT_PRIVATE_PEER_CALL_TYPE, DEFAULT_PEER_CALL_CTRL,
    JITTER_BUFFER_DEPTH, MAX_SYNTH_BURSTS, SLOT_INTERVAL_S,
    HBPF_TGID_TS2, HBPF_UNIT_CALL,
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


def _extract_ambe_from_dmrd(payload_33):
    """DMRD 33-byte voice payload → 19-byte IPSC AMBE block (ipsc2hbp)."""
    burst = bitarray(endian='big')
    burst.frombytes(payload_33)
    if len(burst) < 264:
        burst.extend([0] * (264 - len(burst)))
    a1_72 = burst[0:72]
    a2_72 = burst[72:108] + burst[156:192]
    a3_72 = burst[192:264]
    a1_49 = convert72BitTo49BitAMBE(a1_72)
    a2_49 = convert72BitTo49BitAMBE(a2_72)
    a3_49 = convert72BitTo49BitAMBE(a3_72)
    ipsc_bits = bitarray(152, endian='big')
    ipsc_bits.setall(0)
    ipsc_bits[0:49] = a1_49
    ipsc_bits[50:99] = a2_49
    ipsc_bits[100:149] = a3_49
    return ipsc_bits.tobytes()


def _make_ambe_silence_ipsc():
    silence_72 = bitarray(endian='big')
    silence_72.frombytes(bytes.fromhex('ACAA40200044408080'))
    silence_49 = convert72BitTo49BitAMBE(silence_72)
    bits = bitarray(152, endian='big')
    bits.setall(0)
    bits[0:49] = silence_49
    bits[50:99] = silence_49
    bits[100:149] = silence_49
    return bits.tobytes()


_AMBE_SILENCE_IPSC = _make_ambe_silence_ipsc()


def _dmrd_voice_position(flags):
    """Superframe position from DMRD flags (ipsc2hbp hbp_voice_received)."""
    frame_type = flags & 0x30
    dtype = flags & 0x0F
    if frame_type == HBPF_FRAMETYPE_VOICESYNC:
        return 0
    if dtype == 4:
        return 4
    if dtype >= 5:
        return 5
    return max(dtype, 1)


class IpscVoiceTranslator:
    """Bidirectional IPSC GROUP_VOICE / PRIVATE_VOICE ↔ DMRD for routerHBP / routerIPSC."""

    def __init__(self, master_id=0, ts_prefer_call_info=False):
        self._master_id_b = int(master_id).to_bytes(4, 'big')
        self._peer_call_type = DEFAULT_PEER_CALL_TYPE
        self._peer_private_call_type = DEFAULT_PRIVATE_PEER_CALL_TYPE
        self._peer_call_ctrl = DEFAULT_PEER_CALL_CTRL
        self._ts_prefer_call_info = ts_prefer_call_info
        self._send_cb = None
        self._out_stream_id = {1: None, 2: None}
        self._out_ipsc_stream_id = {1: None, 2: None}
        self._out_seq = 0
        self._out_frame_pos = {1: 0, 2: 0}
        self._out_lc = {1: None, 2: None}
        self._out_emb_lc = {1: None, 2: None}
        self._init_outbound_delivery_state()

    def _init_outbound_delivery_state(self):
        self._del_lc = {1: None, 2: None}
        self._del_emb_lc = {1: None, 2: None}
        self._del_stream_id = {1: 0, 2: 0}
        self._del_hbp_stream = {1: None, 2: None}
        self._del_buf = {1: {}, 2: {}}
        self._del_burst_pos = {1: 0, 2: 0}
        self._del_timer = {1: None, 2: None}
        self._del_next_slot = {1: 0.0, 2: 0.0}
        self._del_consec_synth = {1: 0, 2: 0}
        self._del_rtp_seq = {1: 0, 2: 0}
        self._del_rtp_ts = {1: 0, 2: 0}
        self._del_private = {1: False, 2: False}
        self._del_stream_ctr = 0

    def begin_reflector_encode_session(self):
        """
        Prepare for a new canned reflector speech stream.
        Clears per-call encode state but keeps monotonic IPSC call_seq and RTP
        sequence (repeaters reject duplicate seq if reset every announcement).
        """
        for ts in (1, 2):
            self._cancel_delivery_timer(ts)
            self._del_buf[ts].clear()
            self._del_next_slot[ts] = 0.0
            self._del_burst_pos[ts] = 0
            self._del_consec_synth[ts] = 0
            self._del_hbp_stream[ts] = None
            self._del_lc[ts] = None
            self._del_emb_lc[ts] = None
            self._del_private[ts] = False
            self._del_stream_id[ts] = 0
            self._del_rtp_ts[ts] = 0

    def set_send_callback(self, callback):
        """Register callback(bytes) for paced outbound IPSC voice delivery."""
        self._send_cb = callback

    def learn_peer_header(self, data, private_call=False):
        """Capture call-type / call-control bytes from an inbound voice packet."""
        if len(data) >= 17:
            if private_call:
                self._peer_private_call_type = data[12:13]
            else:
                self._peer_call_type = data[12:13]
            self._peer_call_ctrl = data[13:17]

    def reset(self):
        for ts in (1, 2):
            self._cancel_delivery_timer(ts)
        for ts in (1, 2):
            self._out_stream_id[ts] = None
            self._out_ipsc_stream_id[ts] = None
            self._out_lc[ts] = None
            self._out_emb_lc[ts] = None
            self._out_frame_pos[ts] = 0
        self._out_seq = 0
        self._init_outbound_delivery_state()

    def translate(self, data, ts, burst_type, private_call=False):
        """
        Return a complete DMRD packet (55 bytes) or None if the burst should be skipped.
        private_call: inbound opcode was PRIVATE_VOICE (0x81) — set DMRD unit flag.
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

        if private_call:
            flags |= HBPF_UNIT_CALL

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

    def _build_voice(self, opcode, src_sub, dst_id, call_info, rtp_hdr, voice_payload,
                     call_seq, private_call=False):
        call_type = (
            self._peer_private_call_type if private_call else self._peer_call_type
        )
        return (
            bytes([opcode])
            + self._master_id_b
            + bytes([call_seq])
            + src_sub
            + dst_id
            + call_type
            + self._peer_call_ctrl
            + bytes([call_info])
            + rtp_hdr
            + voice_payload
        )

    def _outbound_opcode(self, ts):
        return PRIVATE_VOICE if self._del_private.get(ts) else GROUP_VOICE

    def _next_rtp_hdr(self, ts, pt, advance_ts=False):
        seq = self._del_rtp_seq[ts]
        ts_val = self._del_rtp_ts[ts]
        rtp_hdr = (
            b'\x80' + bytes([pt])
            + struct.pack('>H', seq & 0xFFFF)
            + struct.pack('>I', ts_val)
            + b'\x00\x00\x00\x00'
        )
        self._del_rtp_seq[ts] = (seq + 1) & 0xFFFF
        if advance_ts:
            self._del_rtp_ts[ts] = (ts_val + 480) & 0xFFFFFFFF
        return rtp_hdr

    def _cancel_delivery_timer(self, ts):
        timer = self._del_timer.get(ts)
        if timer is not None:
            try:
                if timer.active():
                    timer.cancel()
            except Exception:
                pass
        self._del_timer[ts] = None

    def _arm_delivery_timer(self, ts):
        self._cancel_delivery_timer(ts)
        delay = max(0.0, self._del_next_slot[ts] - reactor.seconds())
        self._del_timer[ts] = reactor.callLater(delay, self._delivery_timer_cb, ts)

    def _delivery_timer_cb(self, ts):
        self._del_timer[ts] = None
        if self._del_lc[ts] is None:
            return
        self._deliver_slot(ts)

    def _deliver_slot(self, ts):
        """Deliver one 60 ms TDMA slot to IPSC (real AMBE or synthesized silence)."""
        pos = self._del_burst_pos[ts]
        ambe_19 = self._del_buf[ts].pop(pos, None)

        if ambe_19 is None:
            ambe_19 = _AMBE_SILENCE_IPSC
            self._del_consec_synth[ts] += 1
            if self._del_consec_synth[ts] >= MAX_SYNTH_BURSTS:
                self._synthesize_stream_term(ts)
                return
        else:
            self._del_consec_synth[ts] = 0

        lc = self._del_lc[ts]
        src_sub = lc[6:9]
        dst_id = lc[3:6]
        call_info = TS_CALL_MSK if ts == 2 else 0x00
        gv_payload = _build_slot_voice_payload(ts, pos, ambe_19, self._del_emb_lc[ts], lc)
        self._del_rtp_ts[ts] = (self._del_rtp_ts[ts] + 480) & 0xFFFFFFFF
        rtp_hdr = self._next_rtp_hdr(ts, 0x5d)
        pkt = self._build_voice(
            self._outbound_opcode(ts), src_sub, dst_id, call_info, rtp_hdr, gv_payload,
            self._del_stream_id[ts], private_call=self._del_private[ts],
        )
        if self._send_cb:
            self._send_cb(pkt)

        self._del_burst_pos[ts] = (pos + 1) % 6
        self._del_next_slot[ts] += SLOT_INTERVAL_S
        self._arm_delivery_timer(ts)

    def _flush_del_buf(self, ts):
        """Send all jitter-buffered voice slots immediately (before TERM)."""
        if self._del_lc[ts] is None:
            return
        lc = self._del_lc[ts]
        src_sub = lc[6:9]
        dst_id = lc[3:6]
        call_info = TS_CALL_MSK if ts == 2 else 0x00
        for pos in sorted(self._del_buf[ts].keys()):
            ambe_19 = self._del_buf[ts].pop(pos)
            gv_payload = _build_slot_voice_payload(
                ts, pos, ambe_19, self._del_emb_lc[ts], lc)
            self._del_rtp_ts[ts] = (self._del_rtp_ts[ts] + 480) & 0xFFFFFFFF
            rtp_hdr = self._next_rtp_hdr(ts, 0x5d)
            pkt = self._build_voice(
                self._outbound_opcode(ts), src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=self._del_private[ts],
            )
            if self._send_cb:
                self._send_cb(pkt)
        self._del_buf[ts].clear()

    def _synthesize_stream_term(self, ts):
        lc = self._del_lc[ts]
        if lc is None:
            return
        src_sub = lc[6:9]
        dst_id = lc[3:6]
        call_info = (TS_CALL_MSK if ts == 2 else 0x00) | END_MSK
        gv_payload = bytes([VOICE_TERM]) + _build_ipsc_voice_payload(lc, VOICE_TERM)
        rtp_hdr = self._next_rtp_hdr(ts, 0x5e)
        pkt = self._build_voice(
            self._outbound_opcode(ts), src_sub, dst_id, call_info, rtp_hdr, gv_payload,
            self._del_stream_id[ts], private_call=self._del_private[ts],
        )
        if self._send_cb:
            self._send_cb(pkt)
        self._clear_delivery_ts(ts)

    def _clear_delivery_ts(self, ts):
        self._cancel_delivery_timer(ts)
        self._del_buf[ts].clear()
        self._del_next_slot[ts] = 0.0
        self._del_burst_pos[ts] = 0
        self._del_consec_synth[ts] = 0
        self._del_hbp_stream[ts] = None
        self._del_lc[ts] = None
        self._del_emb_lc[ts] = None
        self._del_private[ts] = False

    def handle_outbound(self, dmrd):
        """
        Process bridged DMRD for IPSC transmit.
        HEAD/TERM are sent immediately; voice bursts are jitter-buffered at 60 ms cadence.
        Returns immediate packet bytes, or None when voice is buffered for paced delivery.
        """
        if len(dmrd) < 53 or dmrd[:4] != DMRD:
            return None

        flags = dmrd[15]
        ts = 2 if (flags & HBPF_TGID_TS2) else 1
        frame_type = flags & 0x30
        dtype = flags & 0x0F
        private_call = bool(flags & HBPF_UNIT_CALL)
        hbp_stream = dmrd[16:20]
        src_sub = dmrd[5:8]
        dst_id = dmrd[8:11]
        payload = dmrd[20:53]

        if frame_type == HBPF_FRAMETYPE_DATASYNC and dtype == HBPF_SLT_VHEAD:
            if (hbp_stream == self._del_hbp_stream.get(ts)
                    and self._del_lc[ts] is not None
                    and self._del_stream_id[ts] is not None):
                # pkt_gen emits 3 HEAD frames; one IPSC HEAD per stream is enough.
                return None
            lc = _decode_lc_from_dmrd(payload)
            self._del_lc[ts] = lc
            self._del_emb_lc[ts] = bptc.encode_emblc(lc)
            self._del_private[ts] = private_call
            if hbp_stream != self._del_hbp_stream.get(ts):
                # New HBP stream — fresh IPSC call-seq; do not arm delivery clock here
                # (ipsc2hbp: first voice burst arms the 120 ms jitter buffer).
                self._del_hbp_stream[ts] = hbp_stream
                self._del_stream_ctr = (self._del_stream_ctr + 1) & 0xFF
                self._del_stream_id[ts] = self._del_stream_ctr
                self._cancel_delivery_timer(ts)
                self._del_buf[ts].clear()
                self._del_burst_pos[ts] = 0
                self._del_consec_synth[ts] = 0
                self._del_next_slot[ts] = 0.0
                self._del_rtp_ts[ts] = 0
            call_info = TS_CALL_MSK if ts == 2 else 0x00
            gv_payload = bytes([VOICE_HEAD]) + _build_ipsc_voice_payload(lc, VOICE_HEAD)
            rtp_hdr = self._next_rtp_hdr(ts, 0xdd)
            return self._build_voice(
                self._outbound_opcode(ts), src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=private_call,
            )

        if frame_type == HBPF_FRAMETYPE_DATASYNC and dtype == HBPF_SLT_VTERM:
            lc = self._del_lc.get(ts) or _decode_lc_from_dmrd(payload) or (LC_OPT + dst_id + src_sub)
            self._cancel_delivery_timer(ts)
            self._flush_del_buf(ts)
            call_info = (END_MSK | TS_CALL_MSK) if ts == 2 else END_MSK
            gv_payload = bytes([VOICE_TERM]) + _build_ipsc_voice_payload(lc, VOICE_TERM)
            rtp_hdr = self._next_rtp_hdr(ts, 0x5e)
            pkt = self._build_voice(
                self._outbound_opcode(ts), src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=self._del_private[ts],
            )
            self._clear_delivery_ts(ts)
            return pkt

        if frame_type in (HBPF_FRAMETYPE_VOICESYNC, HBPF_FRAMETYPE_VOICE):
            if (self._del_lc[ts] is not None
                    and self._del_hbp_stream[ts] is not None
                    and self._del_hbp_stream[ts] != hbp_stream):
                self._clear_delivery_ts(ts)

            if self._del_lc[ts] is None:
                lc = LC_OPT + dst_id + src_sub
                self._del_lc[ts] = lc
                self._del_emb_lc[ts] = bptc.encode_emblc(lc)
                self._del_private[ts] = private_call
                self._del_hbp_stream[ts] = hbp_stream
                self._del_stream_ctr = (self._del_stream_ctr + 1) & 0xFF
                self._del_stream_id[ts] = self._del_stream_ctr

            cur_pos = _dmrd_voice_position(flags)
            self._del_buf[ts][cur_pos] = _extract_ambe_from_dmrd(payload)

            if self._del_timer[ts] is None and self._del_next_slot[ts] == 0.0:
                self._del_burst_pos[ts] = cur_pos
                self._del_consec_synth[ts] = 0
                self._del_next_slot[ts] = reactor.seconds() + JITTER_BUFFER_DEPTH * SLOT_INTERVAL_S
                self._arm_delivery_timer(ts)
            return None

        return None

    def encode(self, dmrd):
        """
        Synchronous encode for unit tests (immediate single-packet build).
        Production path uses handle_outbound() with paced delivery.
        """
        if len(dmrd) < 53 or dmrd[:4] != DMRD:
            return None

        flags = dmrd[15]
        ts = 2 if (flags & HBPF_TGID_TS2) else 1
        frame_type = (flags & 0x30) >> 4
        dtype_vseq = flags & 0x0F
        private_call = bool(flags & HBPF_UNIT_CALL)
        hbp_stream = dmrd[16:20]
        src_sub = dmrd[5:8]
        dst_id = dmrd[8:11]
        payload = dmrd[20:53]

        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
            if (self._del_hbp_stream.get(ts) == hbp_stream
                    and self._del_stream_id[ts]):
                return None
            self._del_hbp_stream[ts] = hbp_stream
            lc = _decode_lc_from_dmrd(payload)
            self._del_stream_ctr = (self._del_stream_ctr + 1) & 0xFF
            self._del_stream_id[ts] = self._del_stream_ctr
            self._del_private[ts] = private_call
            self._del_rtp_ts[ts] = 0
            call_info = TS_CALL_MSK if ts == 2 else 0x00
            gv_payload = bytes([VOICE_HEAD]) + _build_ipsc_voice_payload(lc, VOICE_HEAD)
            rtp_hdr = self._next_rtp_hdr(ts, 0xdd)
            opcode = PRIVATE_VOICE if private_call else GROUP_VOICE
            return self._build_voice(
                opcode, src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=private_call,
            )

        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
            lc = _decode_lc_from_dmrd(payload) or (LC_OPT + dst_id + src_sub)
            call_info = (END_MSK | TS_CALL_MSK) if ts == 2 else END_MSK
            gv_payload = bytes([VOICE_TERM]) + _build_ipsc_voice_payload(lc, VOICE_TERM)
            rtp_hdr = self._next_rtp_hdr(ts, 0x5e)
            opcode = PRIVATE_VOICE if self._del_private.get(ts) else GROUP_VOICE
            pkt = self._build_voice(
                opcode, src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=self._del_private.get(ts, False),
            )
            self._clear_delivery_ts(ts)
            return pkt

        if (frame_type == HBPF_VOICE_SYNC and dtype_vseq == 0) or (
                frame_type == HBPF_VOICE and dtype_vseq in (1, 2, 3, 4, 5)):
            lc = LC_OPT + dst_id + src_sub
            emb_lc = bptc.encode_emblc(lc)
            if self._del_stream_id[ts] == 0:
                self._del_stream_ctr = (self._del_stream_ctr + 1) & 0xFF
                self._del_stream_id[ts] = self._del_stream_ctr
            if private_call:
                self._del_private[ts] = True
            pos = _dmrd_voice_position(flags)
            ambe_19 = _extract_ambe_from_dmrd(payload)
            gv_payload = _build_slot_voice_payload(ts, pos, ambe_19, emb_lc, lc)
            call_info = TS_CALL_MSK if ts == 2 else 0x00
            if pos == 0:
                self._del_rtp_ts[ts] = 0
            self._del_rtp_ts[ts] = (self._del_rtp_ts[ts] + 480) & 0xFFFFFFFF
            rtp_hdr = self._next_rtp_hdr(ts, 0x5d)
            opcode = PRIVATE_VOICE if self._del_private.get(ts) else GROUP_VOICE
            return self._build_voice(
                opcode, src_sub, dst_id, call_info, rtp_hdr, gv_payload,
                self._del_stream_id[ts], private_call=self._del_private.get(ts, False),
            )

        return None
