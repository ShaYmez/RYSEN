# Motorola IPSC in RYSEN

Motorola IP Site Connect (IPSC) support on the **`ipsc`** branch. Field-tested on SYSTEM-XTEST (2026-06) with GB7NR (DR3000), DroidStar hotspot, and BlueDV/OpenBridge.

## What works

### Phase 1 — master & registration

- `MODE: IPSC` system stanza (example: `[IPSC]`)
- IPSC master UDP listener per generated slot (`IPSC-0` … `IPSC-N`)
- Registration (`MASTER_REG_REQ`), keepalives, peer list, de-register
- Inbound group voice → `dmrd_received()` routing (same bridge/rules as HBP masters)
- Optional IPSC HMAC auth (`AUTH_ENABLED` / `AUTH_KEY`)
- `XCMP_XNL` ignored (logged at debug)

### Phase 2a — docker / proxy

- **`ipsc_proxy.py`** — public UDP **56002** (CPS Master port) → backend slots
- **`GENERATOR: 200`** on `[IPSC]` with `PORT: 56003` (backends `56003`–`56202` on compose network)
- Proxy routes by repeater radio ID; master replies by backend source port
- Sample configs: `docker-configs/config/ipsc-proxy-SAMPLE.cfg`, `IPSC-SAMPLE.cfg`

### Phase 2b — bridge parity

- `make_stat_bridge` / `make_single_reflector` include **IPSC-N** slots (same as `SYSTEM-N`)
- `augment_bridges_for_masters()` syncs bridges after `GENERATOR` split
- UA bridge activation and sticky-TG logic apply to IPSC sources
- **Linked IPSC activation** — when a hotspot keys a UA bridge, only an explicitly linked `IPSC-N` leg wakes (`OPTIONS: IPSC=IPSC-198` or `LINK_IPSC=…` on system or peer). IPSC repeaters never auto-wake other legs (restores per-connection isolation).

### Phase 2c — outbound voice

- Bridged DMRD → **GROUP_VOICE** via `IpscVoiceTranslator.handle_outbound()` and `routerIPSC.ipsc_send_system()`
- Motorola extended format (54-byte HEAD/TERM, 52-byte SLOT_VOICE): RTP header, call-control bytes, embedded LC payload (per [ipsc2hbp](https://github.com/n0mjs710/ipsc2hbp))
- Outbound voice uses **60 ms jitter-buffered delivery** — repeaters expect paced TDMA slots, not immediate firehose
- Outbound bytes 1–4 use **`IPSC_MASTER_ID`** (not repeater ID); call-control learned from inbound peer packets via `learn_peer_header()`
- Paced voice sent through `_ipsc_send_voice` callback (Twisted `callLater` timer)

### Field test summary (2026-06, SYSTEM-XTEST)

| Path | Result |
|------|--------|
| GB7NR registration on UDP 56002 + auth | OK |
| Repeater → RYSEN → bridge (TG 2350 TS2) | OK |
| DroidStar → hotspot proxy → `SYSTEM-N` → IPSC → GB7NR | OK (voice + RF key) |
| BlueDV → OBP → bridge → repeater | OK |
| Outbound tcpdump to repeater (~60 ms voice cadence) | OK |

Test subscriber: M0VUB (2345875). Repeater: GB7NR (235287). TG 2350 TS2.

## Architecture

```
DroidStar ──UDP 62031──► hotspot-proxy ──► SYSTEM-N (MASTER)
                                              │
                                              ▼ bridge 2350 TS2
Motorola repeater ──UDP 56002──► ipsc-proxy ──► IPSC-N (routerIPSC)
                                              │
                                              ▼
                                    bridge_master / rules.py

BlueDV ──► OBP-MX (TS1) ──► stat/UA bridge ──► IPSC-N / SYSTEM-N
```

## Configuration

Docker install ships:

| File | Role |
|------|------|
| `rysen.cfg` `[IPSC]` | Backend masters (`PORT` = first backend, `GENERATOR` = slot count) |
| `ipsc-proxy.cfg` | Public listen **56002**, `DESTPORTSTART`/`END` = backend range |
| `rules.py` | Usually empty; static bridges optional (see below) |

| Setting | Purpose |
|---------|---------|
| `PORT` | First backend port (`IPSC-0`); `IPSC-N` uses `PORT + N` |
| `GENERATOR` | Number of backend slots (200 on docker install) |
| `IPSC_MASTER_ID` | Virtual master ID (not the repeater radio ID) |
| `MAX_PEERS` | Peers per slot (`1` recommended) |
| `ALLOWED_PEER_IDS` | Optional whitelist; empty = allow any |
| `ALLOWED_PEER_IPS` | Optional IP whitelist |
| `PROXY_CONTROL` | `PRIN`/`PRCL` logging and proxy disconnect handling |
| `AUTH_ENABLED` / `AUTH_KEY` | HMAC auth — **change key for production** |
| `TS_PREFER_CALL_INFO` | Use call_info byte for TS on SLOT_VOICE if burst_type disagrees (DMRlink confbridge workaround) |
| `KEEPALIVE_WATCHDOG` | Drop IPSC peers with no keepalive (default 60 s) |

### Motorola CPS (e.g. DR3000 peer)

- Link type: **Peer**
- Master IP: your server public IP
- **Master UDP port: 56002** (must match `ipsc-proxy` / firewall / docker-compose)
- **Peer UDP port: 56002** (repeater local bind in CPS)
- IPSC authentication: enabled; key must match `AUTH_KEY` in `rysen.cfg`
- Repeater radio ID in CPS; optional `ALLOWED_PEER_IDS` whitelist

### Optional static bridge (`rules.py`)

Normally UA bridges are created on first PTT. For always-on TG routing without waiting for activation:

```python
BRIDGES = {
    '2350': [
        {'SYSTEM': 'SYSTEM-62', 'TS': 2, 'TGID': 2350, 'ACTIVE': True,
         'TIMEOUT': '', 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
        {'SYSTEM': 'IPSC-79', 'TS': 2, 'TGID': 2350, 'ACTIVE': True,
         'TIMEOUT': '', 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},
    ],
}
```

Slot names (`SYSTEM-62`, `IPSC-79`) change with proxy assignments — check logs. Alternatively set `TS2_STATIC: 2350` on both `[SYSTEM]` and `[IPSC]` in `rysen.cfg`, or hotspot `OPTIONS: IPSC=IPSC-79` to link UA bridges to your repeater only.

## Soak-test checklist

Before merging `ipsc` → `master`:

- [ ] DroidStar cold PTT (after RYSEN restart) — with `OPTIONS: IPSC=IPSC-N` on hotspot, log shows `linked leg activated: IPSC-N`
- [ ] Repeater → hotspot return audio
- [ ] Long call (~2 min) — no stuck bridge / zombie stream
- [ ] Second repeater on another `IPSC-N` slot (proxy ID routing)
- [ ] Auth failure with wrong key (repeater rejected)
- [ ] `tcpdump` during hotspot TX: HEAD 64 B, then ~62 B voice at ~60 ms spacing

## Pre-merge work (not yet done)

See [ipsc-roadmap.md](ipsc-roadmap.md) for the full list. Highlights:

- **Monitor dashboards** — IPSC `PEERS` records are HBP-shaped with alias callsign and protocol metadata; RYMonv3 display polish still pending (see roadmap 2.9–2.10)
- **Selfcare / `ipsc_proxy_v2_sc`**
- **CHANGELOG / version bump** on merge to `master`
- **Production `AUTH_KEY`** rotation off sample defaults
- **Docker Hub** image rebuild from `master` after merge

## Tests

```bash
python -m unittest tests.test_ipsc_phase1 tests.test_ipsc_outbound tests.test_ipsc_proxy tests.test_ipsc_bridge -v
```

## Branch status

Development and field testing on **`ipsc`** — **not merged to `master`**. Soak testing in progress.

Protocol constants and voice translation derived from [ipsc2hbp](https://github.com/n0mjs710/ipsc2hbp) (GPLv3).

## Key source files

| File | Role |
|------|------|
| `ipsc_master.py` | `IpscMasterMixin`, registration, `ipsc_send_system()` |
| `ipsc_voice.py` | `IpscVoiceTranslator` — inbound translate + outbound jitter buffer |
| `ipsc_const.py` | Opcodes, packet lengths, timing constants |
| `ipsc_proxy.py` | Public 56002 front-end |
| `bridge_master.py` | `routerIPSC`, bridge peer-leg activation |
