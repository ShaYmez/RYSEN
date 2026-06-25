# RYSEN DMRMaster+ Changelog

## Unreleased — `ipsc` branch (pre-merge)

Motorola IP Site Connect support. Field-tested on SYSTEM-XTEST (GB7NR, TG 2350 TS2, DroidStar + OpenBridge). **Not merged to `master` yet** — see [doc/ipsc-roadmap.md](doc/ipsc-roadmap.md) for merge blockers (monitor dashboards, soak test).

### New Features

**IPSC master (`MODE: IPSC`)**
- Registration, keepalives, peer list, de-register with optional HMAC auth
- `ipsc_proxy.py` — CPS Master UDP **56002** front-end to `IPSC-0`…`N` backends
- Inbound GROUP_VOICE → standard `dmrd_received()` bridge routing
- Outbound DMRD → Motorola extended GROUP_VOICE (54/52-byte packets, RTP + embedded LC)
- 60 ms jitter-buffered outbound voice delivery (ipsc2hbp model)
- `learn_peer_header()` — echo repeater call-control bytes on outbound
- Bridge parity: `augment_bridges_for_masters()`, UA/stat bridges include IPSC slots
- Linked IPSC UA activation via `OPTIONS: IPSC=` / `LINK_IPSC=` (per-connection isolation; no blanket peer wake)

### Configuration

- `[IPSC]` stanza in `rysen.cfg` / `IPSC-SAMPLE.cfg` (`IPSC_MASTER_ID`, `AUTH_KEY`, `GENERATOR`, etc.)
- Docker compose: `ipsc-proxy` service on port 56002
- Install path: `/opt/rysen-src`, branch `ipsc` — [doc/install.md](doc/install.md)

### Tests

- `tests/test_ipsc_phase1.py`, `test_ipsc_outbound.py`, `test_ipsc_proxy.py`, `test_ipsc_bridge.py`, `test_ipsc_peers.py`

### Known gaps before merge

- **Phase 2 (roadmap):** IPSC `PEERS` records now HBP-shaped; `send_config()` on peer lifecycle (2.1–2.3). FDMR-Monitor field verification still pending — see [doc/ipsc-roadmap.md](doc/ipsc-roadmap.md).
- Selfcare / `ipsc_proxy_v2_sc` not implemented
- Sample `AUTH_KEY` must be changed for production

### Planned post-merge (roadmap)

- Phase 3: `PRIVATE_VOICE (0x81)` — reflector / dial-a-tg over IPSC
- Phase 4: `GROUP_DATA` / `PRIVATE_DATA` — SMS, GPS, UDT
- Phase 5+: TMS, LRRP, ARS (node-dmr-lib reference)

### Key commits (reference)

- Phase 2c outbound voice, extended packet format, jitter buffer, bridge peer-leg fix

---

## Version 1.4.1 (2026-06-09)

### Performance Improvements

**Optimised Bridge Routing**
- Added `BRIDGE_IDX`, an indexed routing map for bridge lookups keyed by system, timeslot, and TGID.
- Replaced repeated full `BRIDGES` scans in the HBP and OBP packet routing hot paths with indexed lookups.
- Added safe full-scan fallback and index rebuild behaviour if an index miss or stale entry is detected.
- Keeps the routing index in sync when bridges are created, replaced, reset, trimmed, or removed.
- Improved scalability for large generated bridge configurations and high talkgroup counts.

**Sticky Talkgroup Timer Optimisation**
- Pre-computes systems with sticky TG enabled before timer processing.
- Avoids scanning the full subscriber map for non-sticky systems during bridge timer maintenance.

### Reliability Improvements

**Duplicate Peer Login Handling**
- Handles duplicate `RPTL`, `RPTK`, and repeater configuration packets without incorrectly resetting peer state.
- Re-sends the correct acknowledgement/challenge for duplicate login attempts from the same peer and socket.
- Updates peer ping timestamps when duplicate authentication/configuration packets are received.
- Only restores default OPTIONS when the final peer disconnects, preventing active peers from losing runtime options.

**Hotspot Proxy Stability**
- Fixed destination port range handling so the configured end port is included.
- Added safer peer cleanup that cancels timers, releases connection tracking entries, and avoids missing-peer errors.
- Handles master NAK/close messages more defensively.
- Added packet length checks before reading peer IDs from incoming proxy packets.
- Fixed DMRA peer ID parsing.
- Uses available-port selection to avoid infinite loops when no proxy ports are free.
- Corrected proxy stats to report the full configured port range.

**Bridge Report Safety**
- Sanitises bridge report payloads before pickling and sending to report clients.
- Skips malformed or incomplete bridge entries with warnings instead of sending unsafe data.
- Normalises bridge trigger lists for safer dashboard/report consumption.

### Diagnostics and Testing

**Routing Diagnostics**
- Added periodic routing statistics for packets, index hits, index misses, fallbacks, bridge count, and index key count.
- Added Twisted reactor lag diagnostics to warn when the event loop falls behind schedule.
- Logs initial bridge index build details at startup.

**Validation Tooling**
- Added `tools/validate_bridge_index.py` to smoke-test routing index behaviour offline.
- Covers index rebuild, add/remove/replace helpers, lookup parity with the old full-scan logic, missing bridge removal, sequence consistency, and large generated-system scenarios.

### Technical Details

**Files Modified**
- `bridge_master.py` - Routing index, hot-path lookup optimisation, timer optimisation, report payload safety, route stats, reactor lag diagnostics.
- `hblink.py` - Duplicate login/config packet handling and safer default OPTIONS restore logic.
- `hdstack/hotspot_proxy_v2.py` - Proxy cleanup, port allocation, defensive packet parsing, and stats fixes.
- `tools/validate_bridge_index.py` - New offline validation/smoke-test tool for bridge index correctness.

### Backwards Compatibility

- Existing bridge rules and configuration formats remain unchanged.
- Routing falls back safely if the bridge index needs rebuilding.
- Existing dashboard/report consumers continue receiving bridge data, now with malformed entries filtered out.
- Hotspot proxy configuration remains compatible while using the full configured port range.

---

## Version 1.4.0 (2026-01-10)

### New Features

**Sticky Talkgroups (Brandmeister-style behavior)**
- System-wide sticky TG support: Configure STICKY_TG: True/False in MASTER system config
- Per-peer sticky TG control: Hotspots can override with STICKY=1 or STICKY=0 in OPTIONS string
- Priority hierarchy: Peer OPTIONS > System STICKY_TG > Default (False)
- Persistent TG behavior: Once a user keys a talkgroup, it remains active until they key a different TG
- No timeout on active TGs: Sticky talkgroups bypass DEFAULT_UA_TIMER for persistent connections
- Static TG priority: TS1_STATIC/TS2_STATIC always override sticky TGs (highest priority)
- Per-user independence: Each subscriber maintains their own sticky TG independently
- Dashboard compatible: FDMR-Monitor correctly displays sticky TG status without modification

### Improvements

**Enhanced SUB_MAP**
- Extended SUB_MAP from 4 to 5 elements: (system, ts, tg, timestamp, peer_id)
- Backwards compatible with 3-element and 4-element formats
- Per-peer tracking enables granular sticky TG control
- Improved subscriber-to-peer linkage for accurate routing

**OPTIONS String Enhancement**
- New STICKY=1 / STICKY=0 keyword support
- Accepts multiple formats: 1/0, true/false, yes/no
- Validation and error logging for invalid values
- Per-peer configuration without server restart

**Configuration**
- New STICKY_TG parameter for MASTER systems
- Explicit configuration required (no silent defaults)
- Clear error messages guide admins to fix incomplete configs
- Documented in sample configs with detailed comments

### Bug Fixes

**PEER System Crash Fix**
- CRITICAL: Fixed KeyError crash on PEER mode systems accessing undefined STICKY_TG
- Added defensive checks: CONFIG['SYSTEMS'][system].get('STICKY_TG', False)
- MODE check ensures STICKY_TG only evaluated for MASTER systems
- PEER systems unaffected by sticky TG logic

**Safe OPTIONS Parsing**
- Added error handling for malformed OPTIONS strings
- Graceful degradation on parse failures
- Invalid STICKY values logged and ignored (doesn't crash)

### Technical Details

**Files Modified**
- bridge_master.py - Core sticky TG logic, SUB_MAP extension, OPTIONS parsing
- config.py - STICKY_TG parameter for MASTER systems
- RYSEN-SAMPLE.cfg - Example configuration with documentation
- RYSEN-SAMPLE-commented.cfg - Detailed sticky TG comments
- hdstack/*.cfg - Updated sample configs
- docker-configs/config/rysen.cfg - Docker config updated
- loro.cfg - Parrot system configuration

**Backwards Compatibility**
- Old config files work (if STICKY_TG added to MASTER sections)
- Old SUB_MAP format supported (3/4/5 element handling)
- Peers without STICKY in OPTIONS use system default
- Systems with STICKY_TG=False behave as before (normal timeout)
- Existing hotspots continue working without OPTIONS changes

### User Documentation

**Enabling Sticky TG (System-Wide)**
```ini
[SYSTEM]
MODE: MASTER
STICKY_TG: True   # Enable for all users
DEFAULT_UA_TIMER: 60
```

**Enabling Sticky TG (Per-Hotspot)**
```ini
# Pi-Star: /etc/pistar-dmr/DMRGateway.ini
[DMR Network 1]
Options=TS1_1=23426;TIMER=10;STICKY=1
```

**Priority Order**
1. Static TGs (TS1_STATIC/TS2_STATIC) - Always highest priority
2. Peer OPTIONS (STICKY=1/0) - User choice overrides system
3. System STICKY_TG (True/False) - Server-wide default
4. Default - False (normal timeout behavior)

### Breaking Changes

**Configuration Requirement:**
- All MASTER systems MUST now include STICKY_TG: True or STICKY_TG: False in config
- Server will error on startup if STICKY_TG missing from MASTER sections
- This is intentional to force explicit configuration

**Migration Steps:**
1. Edit all config files with MASTER systems
2. Add STICKY_TG: False (or True to enable)
3. Typically add after DEFAULT_REFLECTOR line
4. Restart RYSEN

### Recommended Settings

- Normal Talkgroup Systems: STICKY_TG: True
- Test Systems (PARROT): STICKY_TG: False
- Utility Systems: STICKY_TG: False

---

## Version 1.3.9r3 (2024-12-08)

Previous release.
