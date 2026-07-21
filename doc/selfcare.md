# Selfcare

RYSEN selfcare lets operators change repeater and hotspot settings from [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) without editing config files manually. Settings are stored in MariaDB and applied as OPTIONS strings — see [options.md](options.md).

## Overview

| Client type | DB `mode` | RYSEN component | Poll function |
|-------------|-----------|-----------------|---------------|
| IPSC repeater | `0` | `selfcare_db.py` | `ipsc_selfcare_poll()` |
| Hotspot | `> 0` | `proxy_db.py` | Hotspot proxy selfcare poll |

Hotspot proxy poll **excludes** IPSC rows (`mode = 0`).

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
| `rysen.cfg` `[SELF SERVICE]` | rysen (IPSC) | `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`, `ENABLED` |
| `proxy.cfg` `[SELF SERVICE]` | hotspot proxy | `server`, `port`, `username`, `password`, `db_name`, `use_selfservice` |
| `docker-compose.yml` | mariadb + proxy containers | `MYSQL_*`, `DB_*` env vars |

On the compose network, use hostname **`mariadb`** (not `127.0.0.1`).

Enable in config:

```
[SELF SERVICE]
ENABLED: True
```

(hotspot proxy: `USE_SELFSERVICE = True` in `proxy.cfg`)

## IPSC repeater selfcare

When a Motorola repeater registers (`MODE: IPSC`):

1. Row upserted in `Clients` table (`mode = 0`)
2. On dashboard edit, `modified = 1` with OPTIONS string
3. `ipsc_selfcare_poll()` applies `TS1_STATIC` / `TS2_STATIC` via `options_config()`
4. Re-register re-applies stored options

**Multi-static TG example:**
```
TS1=235,23426,116;TS2=2350,2351,2352;RelinkTime=15;
```

`RelinkTime` maps to `DEFAULT_UA_TIMER` (minutes) — IPSC2 convention.

On first register, seed options are built from `TS1_STATIC`/`TS2_STATIC` in `rysen.cfg` if set.

## Hotspot selfcare

The selfcare hotspot proxy (`hotspot_proxy_v2_sc.py`) polls MariaDB for hotspot rows (`mode > 0`) and pushes OPTIONS to the master via RPTO.

Common OPTIONS fields: `TS1=`, `TS2=`, `RelinkTime=`, `STICKY=`, `IPSC=`.

## Remote disconnect (`DISC=1`)

Dashboard can request a disconnect by setting `DISC=1` in the OPTIONS string.

- Applied immediately when RPTO is received
- Also polled from MariaDB within seconds for hotspot rows
- `DISC=1` is stripped after apply (one-shot flag)

Example: `TS2=2350;DISC=1;`

## RYSEN-MONITOR

The dashboard (v1.5.0+) provides:

- IPSC repeater display on Linked Systems
- Multi-static TG selfcare UI
- Hotspot selfcare editing
- Remote disconnect button

Monitor documentation: [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) repository.

## Related docs

- [options.md](options.md) — full OPTIONS syntax
- [ipsc.md](ipsc.md) — IPSC repeater CPS and field tests
- [install.md](install.md) — Docker install and stack upgrade
