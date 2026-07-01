# RYSEN IPSC — roadmap & v1.5.0 release

This document tracks work on the **`ipsc`** branch: what is done, what remains before merge to **`master`** as **version 1.5.0**, and how later features are phased without duplicating bridge logic.

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
| Phase 3 — `PRIVATE_VOICE` wire layer | **Done** (code + unit tests) |
| **Dial-a-tg reflector on IPSC** | **Done** — field-tested GB7NR / SYSTEM-XTEST (2026-06) |
| Short soak before merge | **In progress** (~1 day normal traffic) |
| Merge `ipsc` → `master` + **RYSEN 1.5.0** | **Pending** — soak pass + checklist |

---

## Current schedule

| Window | Work | Outcome |
|--------|------|---------|
| **Now** | **Pre-merge soak** | Group voice + dial-a-tg reflector under normal use (~1 day minimum) |
| **Soak pass** | **v1.5.0 merge** | `ipsc` → `master`, Docker publish, `AUTH_KEY` rotation |
| **Post-merge** | **Phase 4 — unit-to-unit routing** | Private voice between users (not dial-a-tg) |
| **Later** | **Phase 5 — SMS / GPS data** | Not before merge; not targeted for 1.5.0 |

---

## DMR ID numbering (SystemX convention)

Worth documenting for dial-a-tg vs unit-to-unit routing:

| Length | Typical use | Dial-a-tg? |
|--------|-------------|------------|
| **≤ 5 digits** | Talkgroups (max **99999**) | Yes — private-call link targets (e.g. 2350) |
| **6 digits** | Repeater radio IDs (e.g. 235287) | Peer identity, not a link target |
| **7 digits** | Individual subscribers | Unit-to-unit destination |
| **7 digits** | Some hotspots (no SSID suffix) | Hotspot peer IDs exist in practice |
| **9 digits** | Hotspots with SSID suffix (intended) | Not always enforced in field |

**RelinkTime (IPSC2 / DMR+):** selfcare and hotspot OPTIONS use `RelinkTime=` → RYSEN `DEFAULT_UA_TIMER` (minutes). Independent per `[SYSTEM]` and `[IPSC]` slot. Legacy `TIMER=` is also mapped.

---

## Design principles (maintainability)

These rules apply to **every** IPSC phase:

1. **One media path** — Decode IPSC → standard **DMRD** and call `dmrd_received()`. Encode outbound from existing `send_system()`. Do not build parallel “IPSC bridge” or “IPSC reflector” engines.
2. **One peer store for reporting** — `CONFIG['SYSTEMS'][slot]['PEERS']` is the single source of truth for TCP report / FDMR-Monitor.
3. **HBP-shaped peer records** — IPSC peers use the same dict keys as HBP master peers for monitor compatibility.
4. **Shared constants** — Opcodes and flags in `ipsc_const.py`.
5. **Routing master parity** — `MODE: MASTER` and `MODE: IPSC` share bridge/UA timer logic unless protocol forces a difference.
6. **Report on lifecycle** — `send_config()` on IPSC peer register / timeout / de-register.
7. **Tests per opcode family** — Unit tests for each new path.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  bridge_master — bridges, reflectors #N, unit data      │
└───────────────────────────┬─────────────────────────────┘
                            │ DMRD in/out (unchanged semantics)
┌───────────────────────────▼─────────────────────────────┐
│  IPSC media layer                                         │
│  • GroupVoice   0x80  ↔ group DMRD        [Phase 1 — done]│
│  • PrivateVoice 0x81  ↔ unit DMRD         [Phase 3 — done]│
│  • Group/Private Data 0x83/0x84           [Phase 5]       │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  ipsc_master — opcode dispatch, peer lifecycle, auth      │
│  selfcare_db ← MariaDB Clients (mode=0)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  ipsc_proxy (56002) · repeaters · hotspots              │
└─────────────────────────────────────────────────────────┘
```

**Dial-a-tg on IPSC:** same `#NNNN` bridge machinery as HBP; announcements use **PRIVATE_VOICE** back to the caller (reply as the **called ID** — 5000, 4000, link TG). Hotspots still use GROUP on TG 9 via `sendSpeech`.

---

## Phase overview

| Phase | Goal | Status |
|-------|------|--------|
| **0** | Soak + ops | **In progress** — pre-merge soak |
| **1** | Group voice + proxy + bridge | **Done** |
| **2** | Monitor / report + dashboard | **Done** |
| **2d** | IPSC repeater selfcare | **Done** |
| **3** | Private voice wire + dial-a-tg reflector | **Done** (field-tested reflector) |
| **—** | **Merge `ipsc` → `master` as v1.5.0** | After soak |
| **4** | Unit-to-unit private voice routing | **Planned** (post-merge) |
| **5** | Group & private data (SMS, GPS, UDT) | **Deferred** (post-merge) |
| **6** | TMS / LRRP / ARS / wireline | Post-merge |
| **7** | Ops polish | Ongoing |

```
ipsc branch (1.5.0 milestone)
  │  Soak — group voice + dial-a-tg reflector
  │  Monitor spot-check (optional)
  │
Soak pass ─► merge master @ 1.5.0
  ├── Phase 4 unit-to-unit private routing
  └── Phase 5 SMS / GPS (when needed)
```

---

## v1.5.0 merge criteria

| Area | Status | Notes |
|------|--------|-------|
| Repeater registration + keepalive | Done | GB7NR on 56002 + auth |
| Inbound / outbound group voice | Done | TS1 + TS2 |
| Hotspot → IPSC UA bridge | Done | `LINK_IPSC=` |
| IPSC selfcare + static TGs | Done | MariaDB + dashboard |
| Phase 3 private voice encode/decode | Done | `tests/test_ipsc_private_voice.py` |
| **Dial-a-tg reflector on IPSC** | **Done** | 5000/4000/link TG, VTERM+1s, RelinkTime timer |
| Unit-to-unit private routing | **Not in 1.5.0** | Phase 4 — see below |
| SMS / GPS data | **Not in 1.5.0** | Phase 5 |
| Pre-merge soak | **In progress** | Normal traffic ~1 day |
| Production auth defaults | Ops | Rotate `AUTH_KEY` |
| **Merge + version bump** | Done on `ipsc` | `version.txt` **1.5.0**; merge to `master` pending |

---

## Phase 0 — Pre-merge soak

**Goal:** Confirm stability with group voice + dial-a-tg reflector before merge.

### In scope

| Path | Exercise |
|------|----------|
| Repeater → network | Static TGs TS1 + TS2 |
| Network → repeater | OBP / hotspot → IPSC |
| **Dial-a-tg on IPSC** | 5000 status, 4000, link TG, RelinkTime timeout |
| Selfcare | Static TG change + repeater reconnect |
| Resilience | One RYSEN restart; one repeater power-cycle |

### Out of scope for 1.5.0 merge

- Unit-to-unit private calls between subscribers (Phase 4)
- SMS / GPS / UDT (Phase 5)

### Quick log checks

```bash
docker logs systemx -f 2>&1 | grep -E 'Reflector|IPSC reflector|TIMEOUT|linked leg'
docker logs systemx --since 24h 2>&1 | grep -iE 'error|exception|traceback' | tail -5
```

---

## Phase 3 — Private voice & dial-a-tg reflector (**done**)

### Wire layer (done)

| Item | Status |
|------|--------|
| Inbound `0x81` → unit DMRD | Done |
| Outbound unit DMRD → `0x81` TS1 + TS2 | Done |
| Jitter buffer / `_del_private` per TS | Done |
| Unit tests | `tests/test_ipsc_private_voice.py` |

### Dial-a-tg reflector on IPSC (done — field-tested 2026-06)

| Item | Detail |
|------|--------|
| Trigger | Private call to 4000 / 5000 / link TG; speech on **VTERM + 1s** |
| Announcement | PRIVATE_VOICE to originating repeater; **reply as called ID** |
| Timer | `DEFAULT_UA_TIMER` / selfcare **`RelinkTime`** (IPSC2) |
| Timeout voice | Private disconnect prompt (not GROUP TG 9) |
| Hotspot path | Unchanged — GROUP TG 9 via `sendSpeech` |

Tasks:

- [x] **3.1–3.4** Wire layer + tests
- [x] **3.6** Field test: dial-a-tg reflector on IPSC (GB7NR / SYSTEM-XTEST)
- [ ] **3.5** Optional: generic private-call pcap on TS1 + TS2 (transport verify only)

**Not done in Phase 3:** unit-to-unit routing between subscriber IDs — see Phase 4.

---

## Phase 4 — Unit-to-unit private voice (**planned**, post-merge)

**Goal:** Private call from user A to user B (7-digit DMR ID) routes across hotspot ↔ IPSC ↔ hotspot, without treating the destination as dial-a-tg.

### Problem today

`is_reflector_private_destination()` treats almost any ID ≥ 5 as dial-a-tg, so `_forward_unit_voice()` never runs for normal subscriber destinations. Phase 3 transport works; **routing policy** does not.

### Proposed approach

1. **Narrow dial-a-tg detection** — only service codes (4000, 5000, 9991–9999), existing `#` reflector bridges, and **≤5-digit link TGs** — not 6/7/9-digit subscriber or repeater IDs.
2. **Classify destination** using length/convention (see table above) before reflector vs forward.
3. **`_forward_unit_voice()`** — SUB_MAP first, then hotspot 7-digit peer match, then IPSC peer match (destination = registered repeater or subscriber).
4. **Do not** conflate link-TG private call (2350) with unit call (2348831) in the same gate.
5. **Field matrix:** repeater→hotspot, hotspot→repeater, same-system, cross-system; TS1 + TS2.

### Tasks (draft)

- [ ] **4.1** Replace broad `is_reflector_private_destination()` with dial-a-tg-only classifier
- [ ] **4.2** Unit-to-unit forward path + tests (SUB_MAP, peer prefix, IPSC)
- [ ] **4.3** Field test matrix on GB7NR + hotspot
- [ ] **4.4** Document IPSC2 private-call behaviour vs BM hotspot TG 9 model

**Explicitly out of scope for Phase 4:** SMS, GPS, TMS (Phase 5+).

---

## Phase 5 — Group & private data (**deferred**, post-merge)

SMS, GPS, UDT — `GROUP_DATA (0x83)` / `PRIVATE_DATA (0x84)`. Not targeted before merge to `master`.

- [ ] **5.1** Data opcode dispatch in `ipsc_master.py`
- [ ] **5.2** Inbound data → `dmrd_received()` / unit-data path
- [ ] **5.3** Outbound data encode
- [ ] **5.4** Field validation (SMS / GPS)
- [ ] **5.5** Tests mirroring HBP unit-data fixtures

---

## Phase 6 — Motorola services (post-merge)

TMS, LRRP, ARS, optional BMS and wireline (`0xB2`).

---

## Phase 7 — Ops polish (ongoing)

- Voice stream timeout watchdog
- IPSC bridge report events
- Integration tests for reflector timeout / disconnect voice
- Duplicate `VOICE_HEAD` soak validation

---

## Not in scope (v1.5.0)

- Unit-to-unit subscriber routing (Phase 4)
- SMS / GPS / UDT (Phase 5)
- XCMP/XNL (`0x70`)
- CPS remote programming (`0xE0`–`0xE1`)

---

## v1.5.0 release checklist

**Pre-merge (`ipsc` branch)**

- [ ] Pre-merge soak pass (group + dial-a-tg reflector)
- [x] Dial-a-tg reflector on IPSC (field-tested)
- [x] Phase 3 wire layer + tests
- [x] IPSC selfcare + monitor integration
- [ ] Rotate production `AUTH_KEY`
- [ ] Merge `ipsc` → `master` as **v1.5.0**
- [ ] Set `version.txt` to **1.5.0**; finalise CHANGELOG date
- [ ] Rebuild/publish `shaymez/rysen:latest`; installer → `master`

**After 1.5.0**

- [ ] Phase 4: unit-to-unit private routing
- [ ] Phase 5: SMS / GPS when required
- [ ] Phase 6–7: services & hardening

---

## Branch hygiene

| Doc | Purpose |
|-----|---------|
| [ipsc-phase1.md](ipsc-phase1.md) | Feature + field-test reference |
| [install.md](install.md) | Docker install |
| [CHANGELOG.md](../CHANGELOG.md) | Unreleased → **1.5.0** on merge |
