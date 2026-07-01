# Satellite proxy Docker repos

Publish-only repos build slim Docker images; **all proxy development stays in RYSEN** on **`master`**.

| Satellite repo | Docker image | RYSEN source |
|----------------|--------------|--------------|
| [RYSEN-SP-IPSC](https://github.com/ShaYmez/RYSEN-SP-IPSC) | `shaymez/rysen-sp-ipsc:latest` | `master` |
| [RYSEN-SP](https://github.com/ShaYmez/RYSEN-SP) | `shaymez/rysen-sp:latest` | `master` |
| [RYSEN-SP-SELFCARE](https://github.com/ShaYmez/RYSEN-SP-SELFCARE) | `shaymez/rysen-sp-selfcare:latest` | `master` |

## Flow

1. Edit proxy code in RYSEN on **`master`** (e.g. `hotspot_proxy_v2.py`).
2. Push to RYSEN → **Sync satellite proxy repos** runs (path-filtered).
3. Workflow calls `repository_dispatch` on the satellite repo(s) with `ref` = branch pushed.
4. Satellite **Sync from RYSEN** copies files into `sync/`, commits if changed, pushes.
5. Satellite **Build** workflow runs tests, then pushes the Docker image.

## One-time setup (RYSEN)

1. Create a PAT (classic **`repo`** scope, or fine-grained with access to trigger Actions on satellite repos).
2. Add repository secret **`SATELLITE_DISPATCH_TOKEN`** on RYSEN (one secret for all satellites).
3. Ensure `.github/workflows/sync-satellite-repos.yml` is on **`master`**.

## Satellite repo settings

| Repo | Variable | Secrets |
|------|----------|---------|
| **RYSEN-SP-IPSC** | **`RYSEN_SYNC_REF`** = `master` | `DOCKER_USERNAME`, `DOCKER_PASSWORD` |
| **RYSEN-SP** | **`RYSEN_SYNC_REF`** = `master` | `DOCKER_USERNAME`, `DOCKER_PASSWORD` |
| **RYSEN-SP-SELFCARE** | **`RYSEN_SYNC_REF`** = `master` | `DOCKER_USERNAME`, `DOCKER_PASSWORD` |

Push-triggered sync always uses the branch that was pushed. Manual sync uses **`RYSEN_SYNC_REF`** (or the workflow input) when no ref is passed.

## IPSC synced paths

| RYSEN | Satellite |
|-------|-----------|
| `ipsc_proxy.py` | `sync/ipsc_proxy.py` |
| `ipsc_const.py` | `sync/ipsc_const.py` |
| `ipsc-proxy-SAMPLE.cfg` | `sync/ipsc-proxy-SAMPLE.cfg` |
| `tests/test_ipsc_proxy.py` | `tests/test_ipsc_proxy.py` |

Keep `docker-configs/config/ipsc-proxy-SAMPLE.cfg` in sync with the root sample in RYSEN only.

## Hotspot proxy (no selfcare) — RYSEN-SP

| RYSEN | Satellite |
|-------|-----------|
| `hotspot_proxy_v2.py` | `sync/hotspot_proxy_v2.py` |
| `hotspot_proxy_v2-SAMPLE.cfg` | `sync/proxy-SAMPLE.cfg` |
| `tests/test_rysen_sp_proxy.py` | `tests/test_rysen_sp_proxy.py` |

Standalone proxy — no MariaDB / `proxy_db.py`.

## Hotspot proxy (selfcare) — RYSEN-SP-SELFCARE

| RYSEN | Satellite |
|-------|-----------|
| `hotspot_proxy_v2_sc.py` | `sync/hotspot_proxy_v2.py` |
| `proxy_db.py` | `sync/proxy_db.py` |
| `hotspot_proxy_v2_sc-SAMPLE.cfg` | `sync/proxy-SAMPLE.cfg` |
| `tests/test_hotspot_proxy.py` | `tests/test_hotspot_proxy.py` (imports rewritten to `hotspot_proxy_v2`) |

Keep `docker-configs/config/proxy-SAMPLE.cfg` in sync with the selfcare sample in RYSEN only.
