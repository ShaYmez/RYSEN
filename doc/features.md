# RYSEN Features

Version history and feature reference for RYSEN DMRMaster+ (SystemX). Current release: **1.5.0** on `master`.

Maintained by **Shane Daley M0VUB** (aka **ShaYmez**) — primary RYSEN / SystemX development since v1.3.9. Lineage: HBlink3 (N0MJS) → FreeDMR (G7RZU) → RYSEN.

| Version | Date | Summary |
|---------|------|---------|
| 1.5.0 | 2026-06-30 | Motorola IPSC, selfcare, private voice, dial-a-tg on IPSC |
| 1.4.1 | 2026-06-09 | Bridge routing index, proxy stability, diagnostics |
| 1.4.0 | 2026-01-10 | Sticky talkgroups (Brandmeister-style) |
| 1.3.9r3 | 2024-12-08 | SystemX baseline (HBP, OBP, bridges, proxy, Docker) |

Full release notes: [CHANGELOG.md](../CHANGELOG.md).

---

## Core platform (1.3.9r3 baseline)

Inherited from the HBlink3 / FreeDMR lineage, maintained under the RYSEN / SystemX fork.

### Homebrew Protocol (HBP)

- **MASTER** — UDP listener; hotspots and repeaters register as peers
- **PEER** — Outbound connection to another master (parrot, links)
- **GENERATOR** — Expand one config stanza into `SYSTEM-0` … `SYSTEM-N` slots (one UDP port per slot)
- **ACLs** — `REG_ACL`, `SUB_ACL`, `TGID_TS1_ACL`, `TGID_TS2_ACL` (global + per-system)
- **OPTIONS** — Runtime peer/system settings via semicolon strings — see [options.md](options.md)

Config reference: [RYSEN-SAMPLE-commented.cfg](../RYSEN-SAMPLE-commented.cfg).

### OpenBridge (OBP)

- Interconnect to Brandmeister, IPSC2, or other OBP servers
- `MODE: OPENBRIDGE` with `TARGET_IP`, `PASSPHRASE`, `NETWORK_ID`
- `ENHANCED_OBP` for extended bridge behaviour

### Bridges and reflectors

- **`rules.py`** — Static conference bridges between systems/timeslots/TGs
- **UA bridges** — Created on first PTT to a talkgroup
- **Dial-a-tg reflectors** — `#NNNN` bridges; private-call codes 4000 (connect), 5000 (status), link TGs
- **Stat bridges** — Always-on TG routing (`GEN_STAT_BRIDGES`, static TG entries)
- **DEFAULT_REFLECTOR** — Startup reflector per system

### Hotspot proxy

- **`hotspot_proxy_v2.py`** — Single public UDP port → range of backend master ports
- Routes by DMR ID embedded in HBP packets
- Selfcare variant: `hotspot_proxy_v2_sc.py` + MariaDB — see [selfcare.md](selfcare.md)
- Satellite images: `shaymez/rysen-sp`, `shaymez/rysen-sp-selfcare` — [satellite-proxy-repos.md](satellite-proxy-repos.md)

### Subscriber routing

- **SUB_MAP** — Tracks per-subscriber system, timeslot, TG, timestamp, peer
- **Voice announcements** — Multi-language prompts from `Audio/`
- **Alias downloads** — `peer_ids.json`, `subscriber_ids.json`, `talkgroup_ids.json` via `[ALIASES]`

### Reporting

- **`[REPORTS]`** stanza — TCP socket (default port 4321) sends config and bridge state
- Consumed by [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) or compatible dashboards

### Docker deployment

- One-shot install: [install.md](install.md)
- Images: `shaymez/rysen:latest`, `shaymez/rysen-sp-ipsc:latest`
- Config on host: `/etc/rysen/`

---

## v1.4.0 — Sticky talkgroups

Brandmeister-style "last talkgroup" memory per subscriber.

| Setting | Where | Effect |
|---------|-------|--------|
| `STICKY_TG: True/False` | MASTER system in `rysen.cfg` | Server-wide default |
| `STICKY=1/0` | Peer OPTIONS string | Per-hotspot override |

**Priority:** static TGs > peer STICKY > system STICKY_TG > normal timeout.

**Required:** every MASTER stanza must include `STICKY_TG: True` or `STICKY_TG: False`.

See [options.md](options.md) and CHANGELOG 1.4.0.

---

## v1.4.1 — Routing performance

| Feature | Detail |
|---------|--------|
| `BRIDGE_IDX` | Indexed bridge lookups by system + timeslot + TGID |
| Safe fallback | Full scan if index miss; rebuild on bridge changes |
| Proxy stability | Port range fixes, peer cleanup, defensive parsing |
| Diagnostics | Route stats, reactor lag warnings |
| Validation tool | `tools/validate_bridge_index.py` |

---

## v1.5.0 — Motorola IPSC

Full reference: [ipsc.md](ipsc.md). Roadmap for future phases: [ipsc-roadmap.md](ipsc-roadmap.md).

| Feature | Detail |
|---------|--------|
| IPSC master | `MODE: IPSC`, registration, keepalives, optional HMAC auth |
| `ipsc_proxy.py` | Public UDP **56002** → `IPSC-0`…`N` backends |
| Group voice | Inbound/outbound with 60 ms jitter-buffered delivery |
| Bridge parity | IPSC slots in UA/stat bridges; `LINK_IPSC=` per-connection isolation |
| Selfcare | MariaDB static TS1/TS2 for repeaters — [selfcare.md](selfcare.md) |
| Private voice (0x81) | Wire layer on TS1 + TS2; dial-a-tg reflector on IPSC (field-tested) |
| Monitor | HBP-shaped `PEERS` records for RYSEN-MONITOR v1.5.0 |

**Not in 1.5.0:** unit-to-unit private routing (Phase 4), SMS/GPS (Phase 5).

---

## Since v1.5.0 (unreleased)

| Feature | Detail |
|---------|--------|
| `DISC=1` disconnect | Dashboard-triggered hotspot/IPSC disconnect |
| Selfcare apply fixes | Race conditions and stuck "applying" state resolved |
| Dial reflector fix | Numeric TGs no longer paired with dial reflectors on group calls |

---

## Related documentation

- [architecture.md](architecture.md) — stack overview
- [options.md](options.md) — OPTIONS string syntax
- [install.md](install.md) — Docker install
- [selfcare.md](selfcare.md) — MariaDB selfcare
- [hotspot-proxy-v2.md](hotspot-proxy-v2.md) — hotspot proxy setup
- [why-docker.md](why-docker.md) — why Docker is recommended
