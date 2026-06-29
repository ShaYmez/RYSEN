# RYSEN IPSC — roadmap & v1.5.0 release

This document tracks work on the **`ipsc`** branch: what is done, what remains before merge to **`master`** as **version 1.5.0**, and how later features (private call, reflector, SMS, GPS) are phased without duplicating bridge logic.

Field-test reference: [ipsc-phase1.md](ipsc-phase1.md). Protocol research: [node-dmr-lib](https://github.com/rick51231/node-dmr-lib) (MIT; opcode and packet layouts beyond group voice).

**Companion release:** [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) **v1.5.0** is merged to `master` — IPSC repeater display on Linked Systems, multi-static selfcare UI, radio-ID login. RYSEN **1.5.0** on `master` is the matching server release.

---

## Milestone summary (2026-06)

| Area | Status |
|------|--------|
| IPSC group voice (in + out) | **Done** — field-tested GB7NR |
| `ipsc-proxy` on 56002 | **Done** |
| Bridge parity + linked IPSC UA activation | **Done** |
| Monitor peer reporting (HBP-shaped `PEERS`) | **Done** (RYSEN 2.1–2.3) |
| Dashboard IPSC + selfcare | **Done** ([RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) 1.5.0) |
| IPSC repeater selfcare (static TS1/TS2) | **Done** (`selfcare_db.py`, `ipsc_selfcare_poll`) |
| Merge `ipsc` → `master` + **RYSEN 1.5.0** | **Pending** — soak + ops checklist below |

---

## Design principles (maintainability)

These rules apply to **every** IPSC phase, including monitor/report work:

1. **One media path** — Decode IPSC → standard **DMRD** and call `dmrd_received()`. Encode outbound from existing `send_system()` / unit-data paths. Do not build parallel “IPSC bridge” or “IPSC reflector” engines.
2. **One peer store for reporting** — `CONFIG['SYSTEMS'][slot]['PEERS']` is the single source of truth for TCP report / FDMR-Monitor. IPSC runtime state (`_ipsc_peers`) is protocol-internal; any extra fields (keepalive, IPSC mode byte) are mirrored into `PEERS` on change, not sent via a second report channel.
3. **HBP-shaped peer records** — IPSC peers use the same dict keys and types as HBP master peers (`IP`, `PORT`, `SOCKADDR`, string `RADIO_ID`, `CONNECTION`, `LAST_PING`, etc.) so monitors and `options_config()` do not need IPSC-specific parsers. Add optional keys (`PROTOCOL: 'IPSC'`, `LAST_KA`) only when documented.
4. **Shared constants** — Opcodes, burst types, and DMRD flag bytes live in `ipsc_const.py` (extend from node-dmr-lib table). No magic numbers in `ipsc_master.py` / translators.
5. **Routing master parity** — Anything that applies to `MODE: MASTER` routing (bridges, UA timers, `augment_bridges_for_masters()`, `iter_routing_master_systems()`) must treat `MODE: IPSC` the same unless protocol forces a difference.
6. **Report on lifecycle** — Call `send_config()` when IPSC peers register, re-register, time out, or de-register — not only on the periodic reporting loop.
7. **Tests per opcode family** — Each new opcode path gets unit tests; integration tests use the same DMRD fixtures as HBP bridge tests.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  bridge_master — bridges, reflectors #N, unit data, SMS │
└───────────────────────────┬─────────────────────────────┘
                            │ DMRD in/out (unchanged semantics)
┌───────────────────────────▼─────────────────────────────┐
│  IPSC media layer (expand over time)                      │
│  • GroupVoice   0x80  ↔ group DMRD        [Phase 1 — done]│
│  • PrivateVoice 0x81  ↔ unit DMRD         [Phase 3 — next]│
│  • Group/Private Data 0x83/0x84           [Phase 4]       │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  ipsc_master — opcode dispatch, peer lifecycle, auth      │
│  CONFIG['SYSTEMS'][slot]['PEERS'] ← report / monitor      │
│  selfcare_db ← MariaDB Clients (mode=0)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  ipsc_proxy (56002) · repeaters · hotspots              │
└─────────────────────────────────────────────────────────┘
```

Reflector / dial-a-tg: same `#NNNN` machinery as today; IPSC2-style **TG 9 + private call to reflector number** needs **PRIVATE_VOICE (0x81)**, not new bridge rules.

---

## IPSC opcode map (RYSEN vs node-dmr-lib)

| Opcode | Name | RYSEN | Phase |
|--------|------|-------|-------|
| `0x61` | Repeater call transmission | — | 6 (optional) |
| `0x62` | Repeater call control | — | 6 (optional) |
| `0x63` | Repeater block | — | 6 (optional) |
| `0x70` | XCMP / XNL | ignored | — (out of scope for merge) |
| **`0x80`** | **GROUP_VOICE** | **done** | 1 |
| **`0x81`** | **PRIVATE_VOICE** | — | **3** |
| `0x83` | GROUP_DATA | — | 4 |
| `0x84` | PRIVATE_DATA | — | 4 |
| `0x85` | Repeater wake-up | — | 6 |
| `0x90–0x9B` | Reg / alive / peer list / dereg | done | 1 |
| `0x94–0x95` | Peer register req/reply | — | 6 |
| `0xB2` | Wireline (MNIS) | — | 5 |
| `0xE0–0xE1` | Remote programming (CPS) | — | — |

Higher-level services (TMS, LRRP, ARS, BMS) follow node-dmr-lib `DMRServices` — typically over data/wireline, not separate bridge code.

---

## Phase overview

| Phase | Goal | Status |
|-------|------|--------|
| **0** | Soak + ops (auth, CHANGELOG) | In progress |
| **1** | Group voice + proxy + bridge | **Done** |
| **2** | Monitor / report + dashboard | **Done** (server + [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR)) |
| **2d** | IPSC repeater selfcare (static TS1/TS2) | **Done** |
| **—** | **Merge → `master` as v1.5.0** | **Next gate** |
| **3** | Private voice + reflector / dial-a-tg | Post-merge |
| **4** | Group & private data (SMS, GPS, UDT) | Post-merge |
| **5** | TMS / LRRP / ARS / wireline | Post-merge |
| **6** | Ops polish (timeouts, report events) | Ongoing |

```
Done ─────────────► Merge gate (RYSEN v1.5.0)
  │  Phase 0 soak (field)
  │  Final report_receiver / dashboard spot-check
  │  Production AUTH_KEY
  │
Merge ─────────────► master @ 1.5.0
  ├── Phase 3 private voice / reflector
  ├── Phase 4 SMS / GPS data
  └── Phase 5+ services & hardening
```

---

## v1.5.0 merge criteria

| Area | Status | Notes |
|------|--------|-------|
| Repeater registration + keepalive | Done | GB7NR on 56002 + auth |
| Inbound voice (repeater → network) | Done | TG 2350 TS2 |
| Outbound voice (network → repeater) | Done | Extended GROUP_VOICE + 60 ms jitter buffer |
| Hotspot → IPSC UA bridge | Done | Peer-leg auto-activate + `LINK_IPSC=` |
| Docker / `ipsc-proxy` install | Done | See [install.md](install.md) |
| Unit tests (protocol / voice / bridge / selfcare) | Done | `tests/test_ipsc_*.py` |
| Monitor peer reporting (2.1–2.3) | Done | `build_peer_record()`, lifecycle `send_config()` |
| Dashboard IPSC + selfcare UI | Done | RYSEN-MONITOR 1.5.0 on `master` |
| IPSC selfcare (MariaDB `Clients` mode=0) | Done | Register upsert, poll `modified=1`, static TG apply |
| Soak test (multi-day field use) | In progress | — |
| Final VM verify (2.5–2.6) | Recommended | `report_receiver.py` + live dashboard |
| Production auth defaults | Ops | Rotate `AUTH_KEY` off sample |
| **Merge + version bump** | Pending | `version.txt` → **1.5.0**, CHANGELOG, Docker Hub |

---

## Phase 2 — Monitor & report (**done**)

RYSEN reports to TCP clients via `reportFactory.send_config()` (pickled `CONFIG['SYSTEMS']`) and `send_bridge()`.

### RYSEN (server)

- [x] **2.1** `build_peer_record()` shared helper in `hblink.py`; use from `_register_hbp_peer()`.
- [x] **2.2** Mirror `_ipsc_peers` fields into `PEERS` (`LAST_KA` → `LAST_PING`, `PROTOCOL`, `IPSC_MODE`).
- [x] **2.3** `_report.send_config()` on IPSC reg, re-reg, timeout, de-reg.
- [ ] **2.4** Align `ident()` / `options_config()` where safe (optional; document exclusions if deferred).
- [ ] **2.5** Final verify: `report_receiver.py` — `IPSC-N` slot shows repeater when registered.
- [ ] **2.6** Final verify: bridge report shows `IPSC-N` on active conference bridges.
- [x] **2.7** `PEERS` field contract documented (table below).
- [x] **2.8** Unit tests (`tests/test_ipsc_peers.py`).

### Monitor UI ([RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) 1.5.0)

- [x] **2.9** Render `MODE: IPSC` systems in peer panels (Linked Systems / Repeaters).
- [x] **2.10** IPSC metadata display (callsign, Motorola software/hardware, live TS activity).
- [x] Selfcare dashboard — multi-static TS1/TS2, radio-ID login (IPSC `mode=0`).

**Files:** `ipsc_master.py`, `hblink.py`, `bridge_master.py`, `selfcare_db.py`, `tests/test_ipsc_*.py`.

### Target `PEERS` record (IPSC)

| Field | Type | Notes |
|-------|------|-------|
| `CONNECTION` | str | `'YES'` when registered |
| `CONNECTED` | float | Unix time |
| `LAST_PING` | float | Updated on alive / voice |
| `PINGS_RECEIVED` | int | Increment on keepalive |
| `SOCKADDR` | tuple | `(host, port)` |
| `IP` | str | Same as sockaddr host |
| `PORT` | int | Same as sockaddr port |
| `RADIO_ID` | str | Decimal string (match HBP) |
| `CALLSIGN` | bytes | 8-byte padded; from `peer_ids.json` alias when available |
| `SOFTWARE_ID` | bytes | Motorola IPSC protocol version + capabilities (from reg packet) |
| `PACKAGE_ID` | bytes | `Motorola IPSC Repeater` |
| `DESCRIPTION` | bytes | Peer mode summary (digital/mixed, TS1/TS2 routing) |
| `PROTOCOL` | str | `'IPSC'` (optional, for UI) |
| `IPSC_FLAGS` | bytes | Raw 4-byte PeerFlags from registration |
| `IPSC_PROTOCOL` | bytes | Raw 4-byte PeerProtocol from registration |

Registration layout follows [node-dmr-lib](https://github.com/rick51231/node-dmr-lib) (`MasterRegReq`: mode + flags + protocol after peer ID). Motorola does not send MMDVM `RPTC` fields (freq, model serial); use `peer_ids.json` for callsign and optional future `IPSC_PEER_INFO` cfg for static site metadata.

---

## Phase 2d — IPSC repeater selfcare (**done**)

Static TS1/TS2 talkgroups for Motorola repeaters via MariaDB `Clients` (`mode = 0`), coordinated with [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) selfcare UI.

- [x] **`selfcare_db.py`** — upsert on IPSC register, logout on de-register, `modified` flag on re-register when options exist
- [x] **`ipsc_selfcare_poll()`** — periodic poll; `options_config()` on connected IPSC slot
- [x] **`[SELF SERVICE]`** config section in `config.py` / sample cfgs
- [x] **Hotspot proxy isolation** — `proxy_db.py` polls `mode > 0` only (MMDVM selfcare unchanged)
- [x] **Tests** — `tests/test_ipsc_selfcare.py`, `tests/test_static_tg_bridges.py`
- [x] **Field test** — GB7NR multi-static TS1/TS2, reconnect re-apply

Requires MariaDB `selfcare` database and RYSEN-MONITOR stack (not in minimal `docker-compose.yml`; see SYSTEM-XTEST / RYSEN-Installer).

---

## Phase 3 — Private voice & reflector (**next**, post-merge)

- [ ] **3.1** Add `PRIVATE_VOICE = 0x81` and related constants to `ipsc_const.py`; extend `opcode_name()`.
- [ ] **3.2** Decode `0x81` → unit DMRD → `dmrd_received()` (reflector handler on TG 9 + PC).
- [ ] **3.3** Encode outbound private voice; remove blanket unit-call drop in `ipsc_send_system()` (route `0x40` to private encoder).
- [ ] **3.4** Reuse jitter-buffer / stream state from `ipsc_voice.py` where burst cadence matches group voice.
- [ ] **3.5** Field test: TG 9 + private call to reflector on GB7NR; pcap compare with IPSC2.
- [ ] **3.6** Tests: `test_ipsc_private_voice.py`, reflector bridge with IPSC leg.

---

## Phase 4 — Group & private data (post-merge)

SMS, GPS, UDT — decode `GROUP_DATA (0x83)` / `PRIVATE_DATA (0x84)` to unit-data DMRD; wire existing `routerHBP` unit-data handling; outbound encode from bridge send paths. Reference node-dmr-lib NMEA/UDT notes and GB7NR captures.

- [ ] **4.1** Data opcode dispatch in `ipsc_master.py`
- [ ] **4.2** Inbound data → `dmrd_received()` / unit-data path
- [ ] **4.3** Outbound data encode
- [ ] **4.4** Field validation (SMS / GPS)
- [ ] **4.5** Tests mirroring HBP unit-data fixtures

---

## Phase 5 — Motorola services (post-merge)

TMS, LRRP, ARS, optional BMS and wireline (`0xB2`). Implement as services above the data layer (node-dmr-lib `DMRServices` pattern), not inside bridge rules.

---

## Phase 6 — Ops polish (ongoing)

- Voice stream timeout watchdog (ipsc2hbp `check_call_timeouts`)
- IPSC bridge report events (`peer registered`, `timed out`)
- Opcode / per-peer debug stats
- Integration test for jitter-buffer Twisted timer
- Unit test for linked IPSC UA activation (`tests/test_bridge_isolation.py`)
- Duplicate `VOICE_HEAD` soak validation

---

## Not in scope (v1.5.0)

- XCMP/XNL repeater management (`0x70`)
- `ipsc_proxy_v2_sc` standalone selfcare proxy (superseded by integrated `selfcare_db` + RYSEN-MONITOR)
- CPS remote programming (`0xE0`–`0xE1`)

---

## Soak-test log prompts

```bash
# Bridge + linked IPSC activation (per-connection isolation)
docker logs systemx -f 2>&1 | grep -E 'linked leg activated|Bridge 2350 activated for|IPSC peer'

# Hotspot → repeater path
docker logs systemx -f 2>&1 | grep -E 'SYSTEM-[0-9]+.*CALL START|IPSC-[0-9]+.*CALL'

# Selfcare apply
docker logs systemx -f 2>&1 | grep -E 'SELF SERVICE|Applied options for IPSC'

# Outbound to repeater
tcpdump -ni any -c 30 'host <repeater-ip> and udp port 56002'

# Report peer visibility
python3 report_receiver.py -c <rysen-host> <report-port>
```

---

## Branch hygiene

| Doc | Purpose |
|-----|---------|
| [ipsc-phase1.md](ipsc-phase1.md) | Feature + field-test reference |
| [install.md](install.md) | Docker install for `ipsc` branch |
| [CHANGELOG.md](../CHANGELOG.md) | Unreleased → **1.5.0** on merge |

---

## v1.5.0 release checklist

**Pre-merge (`ipsc` branch)**

- [ ] Soak test complete (group voice, bridge, hotspot → IPSC, selfcare reconnect)
- [ ] Phase 2.5–2.6 spot-check on VM (`report_receiver` + dashboard)
- [x] Phase 2 server: HBP-shaped `PEERS` + lifecycle `send_config()`
- [x] Phase 2d: IPSC selfcare + static TG bridges
- [x] RYSEN-MONITOR 1.5.0 merged (dashboard + selfcare UI)
- [ ] Rotate production `AUTH_KEY`
- [ ] Merge `ipsc` → `master`
- [ ] Set `version.txt` to **1.5.0**; finalise CHANGELOG date
- [ ] Rebuild/publish `shaymez/rysen:latest`; update installer to `master` + `docker compose pull`

**Post-merge (v1.5.1+ / Phase 3 branch)**

- [ ] Phase 3: `PRIVATE_VOICE` / reflector over IPSC
- [ ] Phase 4: SMS / GPS data paths
- [ ] Phase 5: TMS / LRRP / ARS as required
- [ ] Phase 6: timeouts, report events, hardening
