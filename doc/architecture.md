# RYSEN Architecture

Overview of the RYSEN DMRMaster+ (SystemX) stack. For install steps see [install.md](install.md); for feature history see [features.md](features.md).

## Stack diagram

```
                    ┌─────────────────────────────────────┐
                    │  Clients                            │
                    │  Hotspots · Motorola repeaters · OBP│
                    └──────────┬──────────────────────────┘
                               │ UDP (HBP / IPSC)
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
     │ hotspot     │   │ ipsc_proxy  │   │ OBP peers   │
     │ proxy       │   │ :56002      │   │ (outbound)  │
     │ :62031…     │   │ → :56003+   │   │             │
     └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
            │                 │                 │
            └────────────────┬┴─────────────────┘
                             ▼
                    ┌─────────────────┐
                    │ bridge_master.py│
                    │  SYSTEM-N slots │
                    │  IPSC-N slots   │
                    │  rules.py       │
                    │  BRIDGE_IDX     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
     │ RYSEN-MONITOR│  │ MariaDB     │  │ Voice       │
     │ TCP :4321   │  │ selfcare    │  │ Audio/      │
     └─────────────┘  └─────────────┘  └─────────────┘
```

## Core process

A single **`bridge_master.py`** process handles all routing. It loads `rysen.cfg` and `rules.py`, listens on configured UDP ports, and dispatches packets through bridge logic.

Optional **`playback.py`** (loro/parrot) runs alongside for echo testing.

Docker entrypoint: [entrypoint](../entrypoint) — starts `bridge_master.py -c rysen.cfg -r rules.py`.

## System slots

| Pattern | MODE | Role |
|---------|------|------|
| `[SYSTEM]` + `GENERATOR: N` | MASTER | HBP masters `SYSTEM-0` … `SYSTEM-(N-1)` |
| `[IPSC]` + `GENERATOR: N` | IPSC | Motorola backends `IPSC-0` … `IPSC-(N-1)` |
| `[OBP-…]` | OPENBRIDGE | Outbound bridge to external network |
| `[PARROT]` etc. | PEER | Outbound HBP peer connections |

Each generated slot gets its own UDP port (`PORT + N`). Hotspot and IPSC proxies multiplex many clients onto these backend ports.

## Proxies

| Component | Image | Public port | Backends |
|-----------|-------|-------------|----------|
| `ipsc_proxy.py` | `shaymez/rysen-sp-ipsc` | 56002 (CPS Master) | 56003–56202 |
| `hotspot_proxy_v2.py` | `shaymez/rysen-sp` | configurable | `SYSTEM-N` ports |
| `hotspot_proxy_v2_sc.py` | `shaymez/rysen-sp-selfcare` | configurable | + MariaDB selfcare poll |

Develop proxy code in this repo; satellite repos publish Docker images — [satellite-proxy-repos.md](satellite-proxy-repos.md).

## Bridge routing

1. Inbound voice/data decoded to standard **DMRD** semantics
2. **`BRIDGE_IDX`** maps (system, timeslot, TGID) → bridge rules (v1.4.1+)
3. **UA bridges** activate on first PTT; **static TGs** stay always-on
4. **Dial-a-tg** reflectors (`#NNNN`) handle private-call service codes
5. Outbound encoding per target protocol (HBP DMRD, IPSC GROUP_VOICE / PRIVATE_VOICE)

## OPTIONS and selfcare

Runtime settings arrive as `KEY=value;` strings — [options.md](options.md).

- Hotspots send OPTIONS via HBP RPTO
- Dashboard writes OPTIONS to MariaDB; RYSEN polls and applies
- IPSC repeaters use `mode = 0` rows; hotspots use `mode > 0`

See [selfcare.md](selfcare.md).

## Reporting path

`[REPORTS]` enables a TCP listener. `bridge_master.py` pickles config and bridge state for connected clients. [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) v1.5.0 displays linked systems, IPSC repeaters, bridge timers, and selfcare UI.

## Deployment options

| Path | Compose file | Contents |
|------|--------------|----------|
| Minimal | `docker-compose.yml` | `rysen` + `ipsc-proxy` (+ optional hotspot profile) |
| Full stack | `docker-compose-stack.yml` | + MariaDB + monitor + selfcare proxy |
| Full SystemX suite | [RYSEN-Installer](https://github.com/shaymez/RYSEN-Installer) | Whiptail menus, Apache, additional services |

Host config directory: `/etc/rysen/` (`rysen.cfg`, `rules.py`, `ipsc-proxy.cfg`, `proxy.cfg`, `docker-compose.yml`).

## Ops scripts

Optional host helpers in [scripts/](../scripts/) (`systemx-start`, `menu`, etc.) for manual stack control. Primary install remains Docker via [install.md](install.md).

## Key source files

| File | Role |
|------|------|
| `bridge_master.py` | Routing, bridges, OPTIONS, selfcare poll |
| `hblink.py` | HBP master/peer protocol |
| `ipsc_master.py` | IPSC registration and opcode dispatch |
| `ipsc_voice.py` | IPSC voice encode/decode + jitter buffer |
| `ipsc_proxy.py` | Public 56002 front-end |
| `hotspot_proxy_v2.py` | HBP hotspot multiplexing |
| `hotspot_proxy_v2_sc.py` | Hotspot proxy + selfcare |
| `selfcare_db.py` | IPSC repeater MariaDB access |
| `proxy_db.py` | Hotspot selfcare MariaDB access |
| `config.py` | Config file parser |
