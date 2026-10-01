# Selfcare

RYSEN selfcare lets operators change repeater and hotspot settings from [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) without editing config files manually. Settings are stored in MariaDB and applied as OPTIONS strings — see [options.md](options.md).

## Overview

| Client type | DB `mode` | RYSEN component | Apply path |
|-------------|-----------|-----------------|------------|
| IPSC repeater | `0` | `selfcare_db.py` | `ipsc_selfcare_poll()` (default 5 s) |
| Hytera repeater | `-1` | `selfcare_db.py` + native RDAC | `ipsc_selfcare_poll()` (default 5 s) |
| Hotspot | `> 0` | `proxy_db.py` + `hotspot_proxy_v2_sc.py` | Proxy `login_opt()` / `send_opts()` → RPTO; DISC via `hotspot_selfcare_disc_poll()` (default 2 s) |

Hotspot proxy poll **excludes** repeater rows (`mode <= 0`).

## Full-stack Docker

Template: [docker-compose-stack.yml](../docker-configs/docker-compose-stack.yml)

Services: `rysen` + `ipsc-proxy` + `proxy` (selfcare) + `mariadb` + `monitor`

Satellite images:
- `shaymez/rysen-sp-selfcare:latest` — hotspot proxy with MariaDB
- Config mount: `/opt/rysen-sp-selfcare/proxy.cfg`

Install steps and credential alignment: [install.md](install.md#selfcare-database-credentials-full-stack).

## Database credentials

Credentials must match across three places:

| File | Read by | Keys |
|------|---------|------|
| `rysen.cfg` `[SELF SERVICE]` | rysen (IPSC and Hytera) | `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`, `ENABLED`, `POLL_INTERVAL`, `DISC_POLL_INTERVAL` |
| `proxy.cfg` `[SELF SERVICE]` | hotspot proxy | `server`, `port`, `username`, `password`, `db_name`, `use_selfservice` |
| `docker-compose.yml` | mariadb + proxy containers | `MYSQL_*`, `DB_*` env vars |

On the compose network, use hostname **`mariadb`** (not `127.0.0.1`).

Enable in config:

```
[SELF SERVICE]
ENABLED: True
```

(hotspot proxy: `use_selfservice = True` in `proxy.cfg` `[SELF SERVICE]`)

Optional poll intervals in `rysen.cfg` `[SELF SERVICE]`:

| Key | Default | Purpose |
|-----|---------|---------|
| `POLL_INTERVAL` | `5` | IPSC selfcare apply poll (seconds) |
| `DISC_POLL_INTERVAL` | `2` | Hotspot `DISC=1` server-side poll (seconds) |

## Repeater selfcare

When a Motorola repeater registers (`MODE: IPSC`):

1. Row upserted in `Clients` table (`mode = 0`)
2. On dashboard edit, `modified = 1` with OPTIONS string
3. `ipsc_selfcare_poll()` applies `TS1_STATIC` / `TS2_STATIC` via `options_config()`
4. On re-register after disconnect or server reboot, `mark_ipsc_options_pending()` sets `modified = 1` from stored DB options so the poll re-applies them (parity with hotspot `login_opt()`)

**Multi-static TG example:**
```
TS1=235,23426,116;TS2=2350,2351,2352;RelinkTime=15;
```

`RelinkTime` maps to `DEFAULT_UA_TIMER` (minutes) — IPSC2 convention.

On first register, seed options are built from `TS1_STATIC`/`TS2_STATIC` in `rysen.cfg` if set.

### Hytera repeaters

Hytera registration follows the same ownership and static-talkgroup lifecycle as
IPSC, using `mode = -1`:

1. A validated RDAC identity creates or refreshes the `Clients` row, preserving
   the existing password and OPTIONS.
2. A first-time owner can claim the account with the repeater callsign or DMR
   ID, then manage static `TS1` and `TS2` talkgroups.
3. Stored options are queued again after a fresh Hytera registration and
   applied through the same repeater poll as IPSC.
4. `DISC=1` remains a one-shot, server-side dynamic-link disconnect request.

RDAC firmware, hardware, serial, callsign, raw channel mode, and TX/RX
frequency are cached in `HyteraMetadata` solely for the PHP selfcare view. The
native Hytera peer report remains the protocol source of truth; the cache is
best-effort and does not control a repeater or write CPS settings.

The dashboard labels these clients **Hytera** and shows the short model prefix
(for example, `RD985`), rather than the full hardware suffix.

## Hotspot selfcare

The selfcare hotspot proxy (`hotspot_proxy_v2_sc.py`) polls MariaDB for hotspot rows (`mode > 0`):

- **`login_opt()`** — 10 s after RPTC login, sends stored `options` to the master via RPTO (reconnect re-apply)
- **`send_opts()`** — pushes non-DISC option updates when `modified = 1`
- **`hotspot_selfcare_disc_poll()`** (in `bridge_master.py`) — applies `DISC=1` from MariaDB without waiting for proxy RPTO

Common OPTIONS fields: `TS1=`, `TS2=`, `RelinkTime=`, `STICKY=`, `IPSC=`.

## Remote disconnect (`DISC=1`)

Dashboard can request a disconnect by setting `DISC=1` in the OPTIONS string.

| Path | How DISC is applied |
|------|---------------------|
| Hotspot RPTO | Immediately when master receives RPTO from proxy or firmware |
| Hotspot MariaDB | `hotspot_selfcare_disc_poll()` (default every 2 s) |
| IPSC or Hytera repeater | `ipsc_selfcare_poll()` only — no RPTO path (default every 5 s) |

After apply, `DISC=1` is stripped from in-memory OPTIONS **and** persisted back to MariaDB (one-shot). Reconnect will not re-fire disconnect.

Example: `TS2=2350;DISC=1;` → stored as `TS2=2350;` after apply.

## RYSEN-MONITOR

The dashboard (v1.5.0+) provides:

- IPSC and Hytera repeater display on Linked Systems
- Multi-static TG selfcare UI
- Hotspot selfcare editing
- Remote disconnect button

Monitor documentation: [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) repository.

## Related docs

- [options.md](options.md) — full OPTIONS syntax
- [ipsc.md](ipsc.md) — IPSC repeater CPS and field tests
- [install.md](install.md) — Docker install and stack upgrade
