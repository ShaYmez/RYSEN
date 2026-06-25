# IPSC branch — roadmap & merge criteria

This document tracks work on the **`ipsc`** branch: what is done, what blocks merge to **`master`**, and how later features (private call, reflector, SMS, GPS) are phased without duplicating bridge logic.

Field-test reference: [ipsc-phase1.md](ipsc-phase1.md). Protocol research: [node-dmr-lib](https://github.com/rick51231/node-dmr-lib) (MIT; opcode and packet layouts beyond group voice).

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
│  • PrivateVoice 0x81  ↔ unit DMRD         [Phase 3]       │
│  • Group/Private Data 0x83/0x84           [Phase 4]       │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  ipsc_master — opcode dispatch, peer lifecycle, auth      │
│  CONFIG['SYSTEMS'][slot]['PEERS'] ← report / monitor      │
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

| Phase | Goal | Merge blocker? |
|-------|------|----------------|
| **0** | Soak + ops (auth, CHANGELOG) | Partial |
| **1** | Group voice + proxy + bridge | **Done** |
| **2** | **Monitor / report consistency** | **Yes — before merge** |
| **3** | Private voice + reflector / dial-a-tg | Post-merge |
| **4** | Group & private data (SMS, GPS, UDT) | Post-merge |
| **5** | TMS / LRRP / ARS / wireline | Post-merge |
| **6** | Ops polish (timeouts, report events) | Ongoing |

```
Now ──────────────► Merge (v1.5.0)
  │  Phase 0 soak
  │  Phase 2 monitor/report
  │
Merge ─────────────► master
  ├── Phase 3 private voice / reflector
  ├── Phase 4 SMS / GPS data
  └── Phase 5+ services & hardening
```

---

## Merge criteria

| Area | Status | Notes |
|------|--------|-------|
| Repeater registration + keepalive | Done | GB7NR on 56002 + auth |
| Inbound voice (repeater → network) | Done | TG 2350 TS2 |
| Outbound voice (network → repeater) | Done | Extended GROUP_VOICE + 60 ms jitter buffer |
| Hotspot → IPSC UA bridge | Done | Peer-leg auto-activate (`4384b6c`) |
| Docker / `ipsc-proxy` install | Done | See [install.md](install.md) |
| Unit tests (protocol / voice / bridge) | Done | `tests/test_ipsc_*.py` |
| Soak test (multi-day field use) | In progress | — |
| **Monitor / report (Phase 2)** | **In progress** | 2.1–2.3 done; verify FDMR-Monitor (2.5–2.6) |
| Selfcare proxy | Not planned for merge | Optional later |
| Production auth defaults | Ops | Rotate `AUTH_KEY` off sample |
| CHANGELOG / version on merge | Pending | 1.5.0 entry when merging |

---

## Phase 2 — Monitor & report (pre-merge)

RYSEN reports to TCP clients (FDMR-Monitor and similar) via `reportFactory.send_config()` (pickled `CONFIG['SYSTEMS']`) and `send_bridge()`.

### Current behaviour

- **HBP masters** (`MODE: MASTER`): peers live in `CONFIG['SYSTEMS'][name]['PEERS']` with a full, stable field set (`IP`, `PORT`, string `RADIO_ID`, `CONNECTION`, …). Dashboards render them.
- **IPSC masters** (`MODE: IPSC`): `routerIPSC` sets `self._peers` to the same `CONFIG['SYSTEMS'][slot]['PEERS']` dict. `_register_hbp_peer()` writes a **minimal** record (missing `IP`/`PORT`, `RADIO_ID` as bytes not string). `_ipsc_peers` holds IPSC-specific `last_ka` / `mode` that never reach the report payload.
- **Periodic loop** (`bridge_master.config_reports`) only logs systems with non-empty `PEERS`; it does not push config on IPSC peer lifecycle events.
- **Bridge reports** already include `IPSC-N` legs via `augment_bridges_for_masters()` — verify dashboards show them.

### Phase 2 tasks

**RYSEN (required for merge)**

- [x] **2.1** Add `build_peer_record()` shared helper in `hblink.py`; use from `_register_hbp_peer()`.
- [x] **2.2** Mirror `_ipsc_peers` fields into `PEERS` (`LAST_KA` → `LAST_PING`, `PROTOCOL`, `IPSC_MODE`).
- [x] **2.3** Call `_report.send_config()` on IPSC reg, re-reg, timeout (`_remove_ipsc_peer`), and de-reg.
- [ ] **2.4** Align `ident()` / `options_config()` where safe: either treat `is_routing_master('IPSC')` like `MASTER` for peer iteration, or document explicit exclusions.
- [ ] **2.5** Verify with `report_receiver.py` (CONFIG) and live FDMR-Monitor: `IPSC-N` slot shows GB7NR when registered.
- [ ] **2.6** Verify bridge report shows `IPSC-N` on active conference bridges.
- [ ] **2.7** Document final `PEERS` field contract in this file (table below).
- [x] **2.8** Unit test: IPSC registration populates `CONFIG['SYSTEMS'][slot]['PEERS']` with HBP-shaped keys (`tests/test_ipsc_peers.py`).

**Monitor UI (external repos — coordinate)**

- [ ] **2.9** Render `MODE: IPSC` systems in peer panels (same as master).
- [ ] **2.10** Optional IPSC label / last-seen from `LAST_PING` or `LAST_KA`.

**Files:** `ipsc_master.py`, `hblink.py` (shared peer record helper), `bridge_master.py` (`config_reports`), `tests/test_ipsc_*.py`.

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
| `CALLSIGN` | bytes | 8-byte padded |
| `PROTOCOL` | str | `'IPSC'` (optional, for UI) |

---

## Phase 3 — Private voice & reflector (post-merge)

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

## Not in scope (initial merge)

- XCMP/XNL repeater management (`0x70`)
- `ipsc_proxy_v2_sc` selfcare — see [ipsc-phase1.md](ipsc-phase1.md)
- CPS remote programming (`0xE0`–`0xE1`)

---

## Soak-test log prompts

```bash
# Bridge + linked IPSC activation (per-connection isolation)
docker logs systemx -f 2>&1 | grep -E 'linked leg activated|Bridge 2350 activated for|IPSC peer'

# Hotspot → repeater path
docker logs systemx -f 2>&1 | grep -E 'SYSTEM-[0-9]+.*CALL START|IPSC-[0-9]+.*CALL'

# Outbound to repeater
tcpdump -ni any -c 30 'host <repeater-ip> and udp port 56002'

# Report peer visibility (after Phase 2)
python3 report_receiver.py -c <rysen-host> <report-port>
```

---

## Branch hygiene

| Doc | Purpose |
|-----|---------|
| [ipsc-phase1.md](ipsc-phase1.md) | Feature + field-test reference |
| [install.md](install.md) | Docker install for `ipsc` branch |
| [CHANGELOG.md](../CHANGELOG.md) | Unreleased `ipsc` section until merge |

---

## Master checklist

**Pre-merge**

- [ ] Soak test complete (group voice, bridge, hotspot → IPSC)
- [ ] Phase 2: IPSC peers visible in FDMR-Monitor / `report_receiver`
- [x] Phase 2: HBP-shaped `PEERS` records + lifecycle `send_config()` (2.1–2.3)
- [ ] Bridge dashboard shows `IPSC-N` legs
- [ ] Rotate production `AUTH_KEY`
- [ ] Merge `ipsc` → `master`, CHANGELOG 1.5.0

**Post-merge**

- [ ] Phase 3: `PRIVATE_VOICE` / reflector over IPSC
- [ ] Phase 4: SMS / GPS data paths
- [ ] Phase 5: TMS / LRRP / ARS as required
- [ ] Phase 6: timeouts, report events, hardening
