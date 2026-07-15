# RYSEN DMRMaster+ Changelog

## Version 1.5.0 (2026-06-30)

Major release: Motorola IP Site Connect for SystemX — group voice, selfcare, monitor integration, and private/unit voice (Phase 3). Field-tested on SYSTEM-XTEST (GB7NR). Companion dashboard: [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) **v1.5.0**.

Released on **`master`** — `shaymez/rysen:latest` and satellite proxy images are published on push.

See [doc/ipsc-roadmap.md](doc/ipsc-roadmap.md) for post-1.5.0 phases. Feature reference: [doc/ipsc.md](doc/ipsc.md).

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
- IPSC peer monitor fields: `peer_ids.json` callsign alias + protocol metadata from registration ([node-dmr-lib](https://github.com/rick51231/node-dmr-lib) layout)
- HBP-shaped `PEERS` records + lifecycle `send_config()` for FDMR-Monitor / RYSEN-MONITOR

**IPSC repeater selfcare**
- `[SELF SERVICE]` — MariaDB `Clients` table (`mode = 0`) upsert on register, logout on de-register
- `ipsc_selfcare_poll()` — apply `TS1_STATIC` / `TS2_STATIC` from dashboard when `modified = 1`
- Re-register re-applies stored options via `mark_ipsc_options_pending()` on new session (poll applies when `modified = 1`)
- Hotspot proxy poll excludes IPSC rows (`proxy_db.py`: `mode > 0` only)

**IPSC private / unit voice (`PRIVATE_VOICE 0x81`) — Phase 3, included in 1.5.0**
- Inbound `0x81` → unit DMRD (`byte 15 |= 0x40`) → existing `dmrd_received()` path
- Outbound unit DMRD → `0x81` on **TS1 and TS2** with shared jitter-buffer delivery
- `ipsc_send_system()` no longer drops unit calls
- `_forward_unit_voice()` hook for cross-system private voice (routing policy — unit-to-unit in Phase 4)

**IPSC dial-a-tg reflector (field-tested GB7NR / SYSTEM-XTEST, 2026-06)**
- Private-call announcements on IPSC: PRIVATE_VOICE reply **as called ID** (5000, 4000, link TG)
- VTERM + 1s delay; monotonic call_seq / RTP seq; RelinkTime / `DEFAULT_UA_TIMER` timeout
- Timeout disconnect voice via private prompt (not GROUP TG 9)
- Hotspot reflector path unchanged (`sendSpeech` on TG 9)

**IPSC / HBP parity polish**
- GLOBAL + system ACL on IPSC inbound; peer cleanup on IPSC timeout
- Unit-data routing includes IPSC peers; reflector timer reset on linked-TG activity
- `sendVoicePacket()` uses correct system reference (HBP reflector speech fix)

### Configuration

- `[IPSC]` stanza in `rysen.cfg` / `IPSC-SAMPLE.cfg` (`IPSC_MASTER_ID`, `AUTH_KEY`, `GENERATOR`, etc.)
- `[SELF SERVICE]` for MariaDB selfcare DB (optional; requires RYSEN-MONITOR stack)
- Docker compose: `ipsc-proxy` service on port 56002
- Install path: Docker Hub `shaymez/rysen:latest` — [doc/install.md](doc/install.md)

### Tests

- `tests/test_ipsc_phase1.py`, `test_ipsc_outbound.py`, `test_ipsc_proxy.py`, `test_ipsc_bridge.py`, `test_ipsc_peers.py`, `test_ipsc_selfcare.py`, `test_static_tg_bridges.py`, `test_ipsc_private_voice.py`

### Planned after 1.5.0 (future releases)

- **Phase 4:** Unit-to-unit private voice routing (subscriber ID → subscriber ID; narrow dial-a-tg classifier)
- **Phase 5:** `GROUP_DATA` / `PRIVATE_DATA` — SMS, GPS, UDT (deferred; not before merge)
- Phase 6+: TMS, LRRP, ARS (node-dmr-lib reference)

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
- `hotspot_proxy_v2.py` - Proxy cleanup, port allocation, defensive packet parsing, and stats fixes.
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
- Sample configs updated (`RYSEN-SAMPLE.cfg`, `docker-configs/config/rysen.cfg`)
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

## Version 1.5.1 (2026-07-15)

Selfcare reliability fixes, documentation overhaul, and legacy install cleanup.

### Bug fixes

- **IPSC selfcare reconnect** — `mark_ipsc_options_pending()` re-queues stored MariaDB options on register so static TGs re-apply after power cycle or server reboot (parity with hotspot `login_opt()`)
- **DISC=1 one-shot** — strip `DISC=1` from in-memory OPTIONS and persist stripped value back to MariaDB after apply (IPSC poll, hotspot DISC poll, HBP RPTO); prevents disconnect re-firing on every reconnect
- **Hotspot ping-timeout** — fix `master_maintenance_loop` empty-peer check (`not self._peers`); align timeout cleanup with RPTCL (reflectors, SUB_MAP, OPTIONS `_reset`)
- **DISC=1 remote disconnect** — dashboard/selfcare can disconnect a hotspot or IPSC repeater; applied on RPTO receipt and via MariaDB poll
- **IPSC selfcare apply** — fix races where settings stuck on "applying"
- **Hotspot selfcare proxy** — `login_opt()` / `send_opts()` use `self.db_proxy`; guard missing `opt_timer` on RPTO before RPTC
- **Dial reflector** — stop pairing numeric talkgroups with dial reflectors on group calls

### Documentation and maintenance

- Selfcare docs: IPSC vs hotspot apply paths, `use_selfservice` key, `POLL_INTERVAL` / `DISC_POLL_INTERVAL`, DISC timing and DB persist
- `DISC_POLL_INTERVAL` parsed in `config.py` (default 2 s)
- Remove unused `find_ipsc_slot_for_radio_id` wrapper, `make_clients_tbl()`, test-only `sanitize_dial_reflectors_for_system` from production helpers
- Full doc overhaul: features, architecture, options, selfcare guides
- Remove legacy install artifacts (hdstack, systemd-scripts, obsolete docker-configs)
- Move ops scripts to `scripts/`; update credits for Shane Daley M0VUB (ShaYmez)

See [doc/selfcare.md](doc/selfcare.md) and [doc/options.md](doc/options.md).

---

## Version 1.3.9r3 (2024-12-08)

Baseline SystemX release before the 1.4.x performance and sticky-TG work. Inherited from the HBlink3 / FreeDMR lineage with RYSEN branding.

### Platform (carried forward)

- **HBP MASTER / PEER** — Homebrew Protocol master and peer modes with `GENERATOR` slot expansion (`SYSTEM-N`)
- **OpenBridge (OBP)** — Brandmeister / IPSC2 interconnect
- **Bridges** — `rules.py` conference bridges, dial-a-tg reflectors (`#NNNN`), UA/stat bridges
- **Hotspot proxy** — UDP port multiplexing (`hotspot_proxy_v2.py`)
- **ACLs** — Global and per-system subscriber, peer, and talkgroup access control
- **SUB_MAP** — Per-subscriber routing state
- **Voice announcements** — Multi-language prompts (`Audio/`)
- **Reporting** — TCP socket for FDMR-Monitor / dashboard (`[REPORTS]`)
- **Docker install** — `docker-configs/docker-compose_install.sh` and `shaymez/rysen` image path

See [doc/features.md](doc/features.md) for the full version history from 1.3.9r3 onward.
