#!/usr/bin/env python3
###############################################################################
#   Motorola IPSC protocol constants (derived from DMRlink / ipsc2hbp)
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

# IPSC opcodes
GROUP_VOICE        = 0x80
PRIVATE_VOICE      = 0x81
MASTER_REG_REQ     = 0x90
MASTER_REG_REPLY   = 0x91
PEER_LIST_REQ      = 0x92
PEER_LIST_REPLY    = 0x93
MASTER_ALIVE_REQ   = 0x96
MASTER_ALIVE_REPLY = 0x97
DE_REG_REQ         = 0x9A
DE_REG_REPLY       = 0x9B
XCMP_XNL           = 0x70

# Burst types inside GROUP_VOICE
VOICE_HEAD  = 0x01
VOICE_TERM  = 0x02
SLOT1_VOICE = 0x0A
SLOT2_VOICE = 0x8A

IPSC_VER = b'\x04\x02\x04\x01'

TS_CALL_MSK = 0b00100000
END_MSK     = 0b01000000

GV_PEER_ID_OFF    = 1
GV_CALL_SEQ_OFF   = 5
GV_SRC_SUB_OFF    = 6
GV_DST_GROUP_OFF  = 9
GV_CALL_INFO_OFF  = 17
GV_BURST_TYPE_OFF = 30
GV_PAYLOAD_OFF    = 31
GV_MIN_LEN        = 31
GV_HEAD_LEN       = 54   # extended VOICE_HEAD / VOICE_TERM (Motorola / ipsc2hbp)
GV_VOICE_LEN      = 52   # extended SLOT_VOICE burst

DEFAULT_PEER_CALL_TYPE = b'\x02'
DEFAULT_PRIVATE_PEER_CALL_TYPE = b'\x01'
DEFAULT_PEER_CALL_CTRL = b'\x00\x00\x43\xe2'

# DMRD byte 15 — unit (private) call flag (matches hblink / bridge_master)
HBPF_UNIT_CALL = 0x40

# Outbound voice delivery (ipsc2hbp translate/const.py)
JITTER_BUFFER_DEPTH = 2      # slots × 60 ms before first voice delivery
MAX_SYNTH_BURSTS = 6         # consecutive silence slots → synthesize TERM
SLOT_INTERVAL_S = 0.060      # TDMA cadence Motorola repeaters expect

AUTH_DIGEST_LEN = 10

# Default capability bytes for IPSC master (DMRlink safe defaults)
DEFAULT_IPSC_MODE_BYTE   = b'\x6A'
DEFAULT_IPSC_FLAGS_BYTES = b'\x00\x00\x00\x05'  # VOICE + MSTR_PEER

# DMRD flags (byte 15) — matches hblink / ipsc2hbp
HBPF_TGID_TS2            = 0x80
HBPF_FRAMETYPE_VOICE     = 0x00
HBPF_FRAMETYPE_VOICESYNC = 0x10
HBPF_FRAMETYPE_DATASYNC  = 0x20

# Proxy control (ASCII — not valid IPSC opcodes)
PRIN = b'PRIN'
PRCL = b'PRCL'

ROUTING_MASTER_MODES = ('MASTER', 'IPSC')


def is_routing_master(mode):
    return mode in ROUTING_MASTER_MODES


def peer_id_from_packet(data):
    """Extract 4-byte radio ID from standard IPSC management/voice packets."""
    if len(data) >= 5:
        return data[1:5]
    return None


def opcode_name(opcode):
    names = {
        GROUP_VOICE: 'GROUP_VOICE',
        PRIVATE_VOICE: 'PRIVATE_VOICE',
        MASTER_REG_REQ: 'MASTER_REG_REQ',
        MASTER_REG_REPLY: 'MASTER_REG_REPLY',
        PEER_LIST_REQ: 'PEER_LIST_REQ',
        PEER_LIST_REPLY: 'PEER_LIST_REPLY',
        MASTER_ALIVE_REQ: 'MASTER_ALIVE_REQ',
        MASTER_ALIVE_REPLY: 'MASTER_ALIVE_REPLY',
        DE_REG_REQ: 'DE_REG_REQ',
        DE_REG_REPLY: 'DE_REG_REPLY',
    }
    return names.get(opcode, '0x{:02x}'.format(opcode))
