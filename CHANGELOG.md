# RYSEN DMRMaster+ Changelog

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
