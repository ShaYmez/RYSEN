# Hotspot Proxy V2

The hotspot proxy is a protocol-aware UDP proxy for Homebrew Protocol (HBP). It accepts connections on a single public UDP port and distributes them to a range of backend master ports — one port per `SYSTEM-N` slot.

Included in the Docker image; satellite images also available — see [satellite-proxy-repos.md](satellite-proxy-repos.md).

## Deployment options

| Image | Selfcare | Config file |
|-------|----------|-------------|
| `shaymez/rysen` (optional profile) | No | `proxy.cfg` |
| `shaymez/rysen-sp:latest` | No | `proxy-SAMPLE.cfg` |
| `shaymez/rysen-sp-selfcare:latest` | Yes (MariaDB) | `proxy-SAMPLE.cfg` |

Docker minimal install enables hotspot proxy optionally:

```bash
docker compose --profile hotspot up -d
```

## Configuration

Config is loaded via **`-c`** flag, not embedded in the Python file.

Sample configs:
- [hotspot_proxy_v2-SAMPLE.cfg](../hotspot_proxy_v2-SAMPLE.cfg)
- [docker-configs/config/proxy-SAMPLE.cfg](../docker-configs/config/proxy-SAMPLE.cfg)

Key settings: listen port, `DESTPORTSTART`/`DESTPORTEND` (backend range matching `GENERATOR` slots in `rysen.cfg`), logging.

## Running manually

```bash
python3 hotspot_proxy_v2.py -c proxy.cfg
```

Selfcare variant:

```bash
python3 hotspot_proxy_v2_sc.py -c proxy.cfg
```

## How it works

The proxy reads the DMR ID from each HBP packet to track which backend port owns each hotspot connection. When a hotspot sends its first packet to the public port, the proxy assigns an available backend port and forwards all subsequent traffic there.

## Related docs

- [architecture.md](architecture.md) — proxy in the stack
- [install.md](install.md) — Docker install
- [selfcare.md](selfcare.md) — selfcare proxy variant
- [options.md](options.md) — hotspot OPTIONS strings

Credits: Simon G7RZU (original proxy); Shane Daley M0VUB aka ShaYmez (RYSEN maintenance and selfcare variant).
