# RYSEN IPSC — Roadmap

Future IPSC work on **`master`**. **v1.5.0 shipped** — see [ipsc.md](ipsc.md), [CHANGELOG.md](../CHANGELOG.md), and [features.md](features.md).

Protocol research: [node-dmr-lib](https://github.com/rick51231/node-dmr-lib). OPTIONS syntax: [options.md](options.md).

## Current focus

| Phase | Goal | Status |
|-------|------|--------|
| **4** | Unit-to-unit private voice routing | **Local on master** (`da29797`, `579d9ed`). Optional global hop is off unless configured |
| **5** | SMS / GPS / UDT data | Deferred |
| **6** | TMS / LRRP / ARS / wireline | Post-merge |
| **7** | Ops polish | Ongoing |

```
master @ 1.5.4 (released)
  ├── Phase 4 local unit voice (shipped) + optional global hop
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

## Phase 4 — Unit-to-unit private voice

**Local delivery is on `master`** (`da29797`, `579d9ed`). A private call to a 7-digit radio is delivered on this master from `SUB_MAP`, then from a connected hotspot ESSID. Hotspot delivery uses TS2. A repeater callee uses the slot they were last heard on. Dial-a-tg (`4000`, `5000`, link TGs) and parrot `9990` stay on their own paths. A busy target slot drops the private call.

**Optional global hop.** With an empty `[ALIASES] UNIT_SUB_MAP_URL` the master behaves as it does for a local-only install. When that URL is set, every private call looks the callee up once per stream, off the reactor. If the returned `opb_net_id` is another master, the unit voice (bit `0x40`, proto 5) goes to that one enhanced OpenBridge peer, even when a hotspot for the same radio is still logged in here. If the hub says this master, or the lookup misses or fails, the call stays on the local slot. The opening frames, including the voice header, are buffered until that answer arrives. A busy local slot drops the call only when the radio is staying here. `[GLOBAL] UNIT_OBP_FLOOD` defaults to false; when true it sends to every other enhanced OpenBridge peer only when there is no home and no local login. `xpeer.freestar.network` is never selected. The destination master still places the callee from its own `SUB_MAP`. Unit data, SMS, and GPS keep their existing path.

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
