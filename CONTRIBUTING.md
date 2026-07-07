# Contributing to RYSEN

RYSEN / SystemX is maintained by **Shane Daley M0VUB** (aka **ShaYmez**). The codebase builds on HBlink3 (N0MJS) and FreeDMR (G7RZU).

## Branch model

- **`master`** — release branch; Docker images publish on push
- Feature branches → pull request → `master`

## Development setup

```bash
git clone https://github.com/ShaYmez/RYSEN.git
cd RYSEN
pip install -r requirements.txt
```

Docker remains the recommended runtime for production. For local dev:

```bash
python bridge_master.py -c RYSEN-SAMPLE.cfg -r rules_SAMPLE.py
```

## Tests

```bash
python -m unittest discover tests -v
```

IPSC subset:

```bash
python -m unittest tests.test_ipsc_phase1 tests.test_ipsc_outbound tests.test_ipsc_proxy \
  tests.test_ipsc_bridge tests.test_ipsc_peers tests.test_ipsc_selfcare \
  tests.test_static_tg_bridges tests.test_ipsc_private_voice -v
```

Bridge index validation:

```bash
python tools/validate_bridge_index.py
```

## Pull requests

- Include tests for new behaviour where practical
- Match existing code style and naming
- Update [CHANGELOG.md](CHANGELOG.md) and relevant `doc/` pages for user-facing changes

## Proxy / satellite workflow

Proxy code (`hotspot_proxy_v2.py`, `ipsc_proxy.py`) is developed **in this repo**. Docker images are published via satellite repos — see [doc/satellite-proxy-repos.md](doc/satellite-proxy-repos.md).

## Ops scripts

Optional host helpers live in [scripts/](scripts/) (`systemx-start`, `menu`, etc.). Full SystemX Whiptail install: [RYSEN-Installer](https://github.com/shaymez/RYSEN-Installer).

## Documentation

All user docs are in [doc/](doc/). When adding features, update [doc/features.md](doc/features.md) and [doc/options.md](doc/options.md) if OPTIONS syntax is affected.
