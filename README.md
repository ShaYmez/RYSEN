# RYSEN DMRMaster+

Open-source DMR master server software (SystemX). A public fork of HBlink3 / FreeDMR, developed in Python (Twisted). **Version 1.5.0** on `master`.

## Quick start

Docker is the recommended install path:

```bash
curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/docker-compose_install.sh | bash
```

Must be run as **root** on Debian 10+, Pi OS, or recent Ubuntu. See [doc/install.md](doc/install.md) for manual steps, full stack (monitor + MariaDB), and upgrades.

## What RYSEN does

- **HBP master** — Hotspots and repeaters on Homebrew Protocol
- **OpenBridge** — Interconnect to Brandmeister / IPSC2
- **Bridges** — Talkgroup routing, dial-a-tg reflectors, static TGs
- **Hotspot proxy** — Single UDP port for many hotspots
- **Motorola IPSC** — IP Site Connect repeaters (v1.5.0)
- **Selfcare** — Dashboard-driven static TG and settings via MariaDB
- **Reporting** — TCP feed for [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR)

## Documentation

| Doc | Description |
|-----|-------------|
| [doc/install.md](doc/install.md) | Docker install, compose, upgrades |
| [doc/features.md](doc/features.md) | Versioned feature catalog |
| [doc/architecture.md](doc/architecture.md) | Stack overview and components |
| [doc/options.md](doc/options.md) | OPTIONS= syntax (TIMER, RelinkTime, DMR+ aliases) |
| [doc/selfcare.md](doc/selfcare.md) | MariaDB selfcare for hotspots and IPSC |
| [doc/ipsc.md](doc/ipsc.md) | Motorola IPSC reference (CPS, config, field tests) |
| [doc/ipsc-roadmap.md](doc/ipsc-roadmap.md) | IPSC future phases |
| [doc/hotspot-proxy-v2.md](doc/hotspot-proxy-v2.md) | Hotspot UDP proxy |
| [doc/why-docker.md](doc/why-docker.md) | Why Docker is recommended |
| [doc/satellite-proxy-repos.md](doc/satellite-proxy-repos.md) | Satellite proxy image workflow |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

Sample configs: [RYSEN-SAMPLE-commented.cfg](RYSEN-SAMPLE-commented.cfg), [docker-configs/config/rysen.cfg](docker-configs/config/rysen.cfg).

## Related projects

- [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) — Dashboard (v1.5.0+)
- [RYSEN-Installer](https://github.com/shaymez/RYSEN-Installer) — Full SystemX suite with menus
- Docker images: `shaymez/rysen:latest`, `shaymez/rysen-sp-ipsc:latest`, `shaymez/rysen-sp-selfcare:latest`

## Docker image

CI builds and publishes `shaymez/rysen:latest` from the root [Dockerfile](Dockerfile) on push to `master`.

## Credits

**Lineage**
- **HBlink3** — Cortney Buffington N0MJS
- **FreeDMR** — Simon Adlem G7RZU

**RYSEN / SystemX** — **Shane Daley M0VUB** (aka **ShaYmez**), primary development 2024–present: Motorola IPSC, selfcare, Docker deployment, bridge routing, and field-tested SystemX stack.

Additional contributors: Eric K7EEL and others credited in source file headers.

**Property:** Implementation of the HomeBrew Repeater Protocol and related DMR protocols. See file headers and [LICENSE.txt](LICENSE.txt) (GPLv3).

**Warranty:** None. Use at your own risk.

Copyright (C) 2016–2026 Cortney T. Buffington, N0MJS; 2024–2026 Shane Daley M0VUB (ShaYmez); and contributors.
