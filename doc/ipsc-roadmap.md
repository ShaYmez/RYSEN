# RYSEN IPSC — Roadmap

Future IPSC work on **`master`**. **v1.5.0 shipped** — see [ipsc.md](ipsc.md), [CHANGELOG.md](../CHANGELOG.md), and [features.md](features.md).

Protocol research: [node-dmr-lib](https://github.com/rick51231/node-dmr-lib). OPTIONS syntax: [options.md](options.md).

## Current focus

| Phase | Goal | Status |
|-------|------|--------|
| **4** | Unit-to-unit private voice routing | **Planned** (current) |
| **5** | SMS / GPS / UDT data | Deferred |
| **6** | TMS / LRRP / ARS / wireline | Post-merge |
| **7** | Ops polish | Ongoing |

```
master @ 1.5.0 (released)
  ├── Phase 4 unit-to-unit private routing  ← current
  └── Phase 5 SMS / GPS (when needed)
```

## DMR ID numbering (SystemX convention)

| Length | Typical use | Dial-a-tg? |
|--------|-------------|------------|
| **≤ 5 digits** | Talkgroups (max **99999**) | Yes — link targets (e.g. 2350) |
| **6 digits** | Repeater radio IDs (e.g. 235287) | Peer identity, not a link target |
| **7 digits** | Individual subscribers | Unit-to-unit destination |
| **9 digits** | Hotspots with SSID suffix | Hotspot peer IDs |

**RelinkTime (IPSC2 / DMR+):** `RelinkTime=` in OPTIONS → `DEFAULT_UA_TIMER` (minutes). Legacy `TIMER=` also accepted.

## Design principles

1. **One media path** — IPSC → DMRD → `dmrd_received()`; outbound from existing `send_system()`
2. **One peer store** — `CONFIG['SYSTEMS'][slot]['PEERS']` for reporting
3. **HBP-shaped peer records** — monitor compatibility
4. **Shared constants** — `ipsc_const.py`
5. **Routing parity** — MASTER and IPSC share bridge/UA timer logic
6. **Report on lifecycle** — `send_config()` on register / timeout / de-register
7. **Tests per opcode family**

## Architecture

```
bridge_master — bridges, reflectors #N
        │ DMRD in/out
IPSC media layer
  • GroupVoice   0x80  [done]
  • PrivateVoice 0x81  [done]
  • Data 0x83/0x84     [Phase 5]
        │
ipsc_master — auth, lifecycle, selfcare_db
        │
ipsc_proxy (56002) · repeaters · hotspots
```

## Phase 4 — Unit-to-unit private voice (planned)

**Goal:** Private call from user A to user B (7-digit DMR ID) across hotspot ↔ IPSC ↔ hotspot.

**Problem:** `is_reflector_private_destination()` treats many IDs as dial-a-tg; `_forward_unit_voice()` does not run for subscriber destinations. Phase 3 wire layer works; routing policy does not.

**Proposed approach:**

1. Narrow dial-a-tg detection to service codes (4000, 5000, 9991–9999), `#` reflectors, and ≤5-digit link TGs
2. Classify destination by length before reflector vs forward
3. `_forward_unit_voice()` — SUB_MAP, then hotspot peer, then IPSC peer
4. Field matrix: repeater↔hotspot, same/cross-system, TS1 + TS2

Tasks: classifier rewrite, forward path + tests, field test, document IPSC2 vs BM TG 9 model.

## Phase 5 — Group & private data (deferred)

`GROUP_DATA (0x83)` / `PRIVATE_DATA (0x84)` — SMS, GPS, UDT.

## Phase 6 — Motorola services

TMS, LRRP, ARS, BMS, wireline (`0xB2`).

## Phase 7 — Ops polish

Voice stream timeout watchdog, IPSC bridge report events, reflector timeout integration tests.

## Not in scope

- XCMP/XNL (`0x70`)
- CPS remote programming (`0xE0`–`0xE1`)

## Ops reminder

Rotate production `AUTH_KEY` off sample defaults before field deployment.

## Related docs

| Doc | Purpose |
|-----|---------|
| [ipsc.md](ipsc.md) | v1.5.0 feature reference |
| [install.md](install.md) | Docker install |
| [selfcare.md](selfcare.md) | MariaDB selfcare |
| [CHANGELOG.md](../CHANGELOG.md) | Release notes |
