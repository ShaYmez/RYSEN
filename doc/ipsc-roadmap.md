# RYSEN IPSC — Roadmap

Future IPSC work on **`master`**. Group voice, selfcare, and Phase 3 private voice shipped in **v1.5.0**. Phase 4 unit routing shipped in **v1.6.0**. See [ipsc.md](ipsc.md), [CHANGELOG.md](../CHANGELOG.md), and [features.md](features.md).

Protocol research: [node-dmr-lib](https://github.com/rick51231/node-dmr-lib). OPTIONS syntax: [options.md](options.md).

## Current focus

| Phase | Goal | Status |
|-------|------|--------|
| **4** | Unit-to-unit private voice routing | **Shipped in 1.6.0.** Local delivery, plus optional global hop (off unless configured) |
| **5** | SMS / GPS / UDT data | Deferred |
| **6** | TMS / LRRP / ARS / wireline | Post-merge |
| **7** | Ops polish | Ongoing |

```
master @ 1.6.0 (released)
  ├── Phase 4 local unit voice + optional global hop (shipped)
  └── Phase 5 SMS / GPS (deferred)
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

**Local delivery shipped in 1.6.0** (first landed in `da29797`, `579d9ed`). Operator reference: [unit-call.md](unit-call.md). A private call to a 7-digit radio is delivered on this master from `SUB_MAP`, then from a connected hotspot ESSID. Hotspot delivery uses TS2. A repeater callee uses the slot they were last heard on. Dial-a-tg (`4000`, `5000`, link TGs) and parrot `9990` stay on their own paths. A busy target slot drops the private call.

**Optional global topology.** With an empty `[ALIASES] UNIT_SUB_MAP_URL` the master keeps its exact local-only behaviour: no topology HTTP and no OBP unit relay. A participating master uses the existing token file, reports a live secret-free snapshot of its configured proto-v5 enhanced-OBP links off the reactor, and accepts only masters found in the downloaded server registry. The hub route names the home, immediate next hop, complete path, topology version and expiry. RYSEN validates that path, forwards voice to exactly one healthy next hop, never sends back to ingress, and never floods or falls back through a third-party stanza. A failed next hop gets a bounded buffer and limited re-lookup. The destination master tries its own `SUB_MAP`/peer placement first; only a local miss may transit again. DMRE v5 `source_server`, `source_rptr`, and the existing hop counter remain end to end, with the protocol's existing maximum of 10. Unit data, SMS, and GPS keep their existing path.

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
| [unit-call.md](unit-call.md) | Private unit calls, local and optional fleet hop |
| [ipsc.md](ipsc.md) | IPSC feature reference |
| [install.md](install.md) | Docker install |
| [selfcare.md](selfcare.md) | MariaDB selfcare |
| [CHANGELOG.md](../CHANGELOG.md) | Release notes |
