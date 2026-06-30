# Satellite proxy Docker repos

Publish-only repos build slim Docker images; **all proxy development stays in RYSEN**.

| Satellite repo | Docker image | RYSEN source (today) | After `ipsc` merge |
|----------------|--------------|----------------------|--------------------|
| [RYSEN-SP-IPSC](https://github.com/ShaYmez/RYSEN-SP-IPSC) | `shaymez/rysen-sp-ipsc:latest` | `ipsc` branch | `master` |
| [RYSEN-SP-SELFCARE](https://github.com/ShaYmez/RYSEN-SP-SELFCARE) | `shaymez/rysen-sp-selfcare:latest` | `master` (sync TBD) | `master` |

## Flow

1. Edit proxy code in RYSEN (e.g. `ipsc_proxy.py` on `ipsc`).
2. Push to RYSEN → **Sync satellite proxy repos** runs (path-filtered).
3. Workflow calls `repository_dispatch` on the satellite repo with `ref` = branch pushed.
4. Satellite **Sync from RYSEN** copies files into `sync/`, commits if changed, pushes.
5. Satellite **Build** workflow runs tests, then pushes the Docker image.

## One-time setup (RYSEN)

1. Create a PAT (classic `repo` scope, or fine-grained with access to trigger Actions on satellite repos).
2. Add repository secret **`SATELLITE_DISPATCH_TOKEN`** on RYSEN.
3. Ensure `.github/workflows/sync-satellite-repos.yml` is on the branch you develop on (`ipsc` for IPSC work).

## Satellite repo settings

- **RYSEN-SP-IPSC**: variable **`RYSEN_SYNC_REF`** = `ipsc` (change to `master` after merge); Docker Hub secrets for build.
- **RYSEN-SP-SELFCARE** (when refactored): same pattern; sync paths likely `hotspot_proxy_v2_sc.py`, `proxy_db.py`, `hotspot_proxy_v2_sc-SAMPLE.cfg` → `sync/` in the satellite repo.

## IPSC synced paths

| RYSEN | Satellite |
|-------|-----------|
| `ipsc_proxy.py` | `sync/ipsc_proxy.py` |
| `ipsc_const.py` | `sync/ipsc_const.py` |
| `ipsc-proxy-SAMPLE.cfg` | `sync/ipsc-proxy-SAMPLE.cfg` |
| `tests/test_ipsc_proxy.py` | `tests/test_ipsc_proxy.py` |

Keep `docker-configs/config/ipsc-proxy-SAMPLE.cfg` in sync with the root sample in RYSEN only (not copied to the satellite repo).
