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

### Phase 2d — monitor reporting

- `build_peer_record()` — HBP-compatible `PEERS` dicts for IPSC (`PROTOCOL`, string `RADIO_ID`, `IP`/`PORT`)
- Lifecycle `send_config()` on register, re-register, timeout, de-register
- Dashboard: [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) **v1.5.0** — IPSC repeaters on Linked Systems, live TS activity

### Phase 2e — IPSC repeater selfcare

- `[SELF SERVICE]` + `selfcare_db.py` — MariaDB `Clients` (`mode = 0`)
- Register upsert / de-register logout; poll `modified = 1` → `options_config()` static TS1/TS2
- Multi-static TG strings (`TS1=235,23426,116;TS2=2350,2351,2352;`) via RYSEN-MONITOR selfcare UI
- Reconnect re-applies options when stored in DB

### Field test summary (2026-06, SYSTEM-XTEST)

| Path | Result |
|------|--------|
| GB7NR registration on UDP 56002 + auth | OK |
| Repeater → RYSEN → bridge (TG 2350 TS2) | OK |
| DroidStar → hotspot proxy → `SYSTEM-N` → IPSC → GB7NR | OK (voice + RF key) |
| BlueDV → OBP → bridge → repeater | OK |
| Outbound tcpdump to repeater (~60 ms voice cadence) | OK |
| IPSC selfcare multi-static TS1/TS2 + reconnect | OK |
| RYSEN-MONITOR dashboard IPSC + selfcare | OK (monitor v1.5.0) |

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

## Soak-test checklist (1 week — started 2026-06-24)

**In scope:** group voice only. Unit/private calls come in **Phase 3** after v1.5.0 merge.

### Traffic to run during the week

- [ ] **TS1** — PTT on each static TG in selfcare `TS1=` list
- [ ] **TS2** — PTT on each static TG in selfcare `TS2=` list (e.g. 2350)
- [ ] Repeater → network audio (inbound GROUP_VOICE)
- [ ] OBP / hotspot → repeater audio (outbound GROUP_VOICE)
- [ ] DroidStar cold PTT after RYSEN restart — `OPTIONS: IPSC=IPSC-N`; log shows `linked leg activated`
- [ ] Repeater → hotspot return audio
- [ ] Long call (~2 min) — no stuck bridge / zombie stream
- [ ] Repeater power-cycle — registration + selfcare static TGs re-applied
- [ ] RYSEN restart mid-week — repeater re-registers; voice still works
- [ ] Dashboard shows GB7NR on Linked Systems throughout

### One-off checks

- [ ] Auth failure with wrong key (test repeater rejected)
- [ ] `tcpdump` during hotspot TX: HEAD ~54 B, voice ~52 B at ~60 ms spacing
- [ ] `report_receiver.py` — IPSC slot + bridge legs (roadmap 2.5–2.6)

### Not expected this week

- [ ] Unit/private voice through IPSC (Phase 3 — TS1 and TS2)

## Pre-merge work (v1.5.0 release gate)

See [ipsc-roadmap.md](ipsc-roadmap.md) for the full checklist. Remaining:

- **Soak test** — **1-week soak in progress** (from 2026-06-24); group voice TS1+TS2
- **After soak** — merge **v1.5.0**, then **Phase 3** unit calls on TS1 and TS2
- **Final verify** — `report_receiver.py` + dashboard spot-check (roadmap 2.5–2.6)
- **Production `AUTH_KEY`** — rotate off sample defaults
- **Merge `ipsc` → `master`** — bump to **RYSEN 1.5.0**, publish Docker image

**Done:** monitor reporting, RYSEN-MONITOR 1.5.0 dashboard/selfcare, IPSC static TG selfcare.

## Tests

```bash
python -m unittest tests.test_ipsc_phase1 tests.test_ipsc_outbound tests.test_ipsc_proxy tests.test_ipsc_bridge tests.test_ipsc_peers tests.test_ipsc_selfcare tests.test_static_tg_bridges -v
```

## Branch status

Development on **`ipsc`** — targets **v1.5.0** merge to `master`. Feature-complete for group voice + selfcare; soak testing in progress.

Protocol constants and voice translation derived from [ipsc2hbp](https://github.com/n0mjs710/ipsc2hbp) (GPLv3).

## Key source files

| File | Role |
|------|------|
| `ipsc_master.py` | `IpscMasterMixin`, registration, `ipsc_send_system()` |
| `ipsc_voice.py` | `IpscVoiceTranslator` — inbound translate + outbound jitter buffer |
| `ipsc_const.py` | Opcodes, packet lengths, timing constants |
| `ipsc_proxy.py` | Public 56002 front-end |
| `selfcare_db.py` | IPSC repeater `Clients` DB + static TG options |
| `bridge_master.py` | `routerIPSC`, `ipsc_selfcare_poll()`, bridge peer-leg activation |
