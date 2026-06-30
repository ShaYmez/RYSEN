# \*\*The current recommended way of installing RYSEN Master+ is in docker. \*\*

Docker is the fastest way to having a running system, complete with proxy and echo, ready to serve repeaters and hotspots. The Docker image can be run on any system that can run Linux docker containers. We recommend Debian 11.

Not convinced? Read [Why Docker?](https://github.com/ShaYmez/RYSEN/blob/ipsc/doc/why-docker.md)

# Quick Start

For a one-shot install on a Debian or Debian-based system, paste this into the terminal of a running debian-like Linux system. This will install RYSEN Master+ only. For the RYSEN Master+ Suite scroll down to the bottom.

*Important!*
Must be root!! for this install to work correctly! Sudo is for amateurs!

`curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/ipsc/docker-configs/docker-compose_install.sh | bash`

This works on Debian 10, 11, 12, PiOs (Raspbian) and recent Ubuntu systems, on other flavours, you may need to follow the process below.

The installer uses Docker Compose V2 (`docker compose`). On the **ipsc** branch the installer clones source to `/opt/rysen-src` and **builds the image locally** (no Docker Hub required for testing).

The rest of this page details the manual install process.

## Prerequisites

Firstly, you need a system running docker with the Compose V2 plugin. Follow the instructions for your distro [here](https://docs.docker.com/engine/install/debian/).

Install the recommended packages:

`apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`

A change needs to be made to the docker config for openbridge to work correctly:

`echo '{ "userland-proxy": false}' > /etc/docker/daemon.json`

`systemctl restart docker`

## Grab and edit the config file

Get the file: [rysen.cfg](https://github.com/ShaYmez/RYSEN/blob/ipsc/docker-configs/config/rysen.cfg)

`mkdir /etc/rysen`

place the rysen.cfg file in this directory.

For IPSC, the docker install starts **rysen** and **ipsc-proxy** (public UDP **56002** CPS Master port; backends `IPSC-0`…`IPSC-199` on `56003`–`56202`). Enable IPSC auth in CPS and match `AUTH_KEY` in `rysen.cfg`. IPSC repeater selfcare and the dashboard require [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) (v1.5.0+) and MariaDB — see monitor repo docs; minimal compose here is RYSEN + proxy only. See [ipsc-phase1.md](ipsc-phase1.md), [ipsc-roadmap.md](ipsc-roadmap.md) (v1.5.0 release checklist), [IPSC-SAMPLE.cfg](../docker-configs/config/IPSC-SAMPLE.cfg), and [ipsc-proxy-SAMPLE.cfg](../docker-configs/config/ipsc-proxy-SAMPLE.cfg).

## Make rules file

`echo "BRIDGES = {'9990': [{'SYSTEM': 'ECHO', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/rysen/rules.py`

## Make JSON directory

`mkdir -p /etc/rysen/json`

## Make sure docker container can access config and rules ##

`chmod 755 /etc/rysen -R`

## create /etc/rysen/docker-compose.yml

Download the file here: [docker-compose.yml](https://github.com/ShaYmez/RYSEN/raw/ipsc/docker-configs/docker-compose.yml)

Clone source for local build (ipsc branch testing):

```
git clone -b ipsc --depth 1 https://github.com/ShaYmez/RYSEN.git /opt/rysen-src
```

## Add network tuning

Create `/etc/sysctl.d/99-rysen.conf` with:

```
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
```

Run command:

`sysctl --system`

## Run RYSEN Master+

`cd /etc/rysen`

`docker compose build rysen`

`docker compose up rysen`

Once you are sure it has run correctly, you can restart in the background

`docker compose up -d rysen`

Optional hotspot proxy (not required for IPSC):

`docker compose --profile hotspot up -d`

## Stop docker container

`docker compose down`

## Full stack (test / production VM with monitor + MariaDB)

Template: [docker-compose-stack.yml](../docker-configs/docker-compose-stack.yml) — `rysen` + `ipsc-proxy` + `proxy` (selfcare) + `mariadb` + `monitor`. Copy to `/etc/rysen/docker-compose.yml` and **keep your existing** `rysen.cfg`, `ipsc-proxy.cfg`, `proxy.cfg`, MariaDB data under `/etc/rysen/mysql`, and monitor config.

Satellite images (pulled, not built locally):

- `shaymez/rysen-sp-ipsc:latest` — config mount `/opt/rysen-sp-ipsc/ipsc-proxy.cfg`
- `shaymez/rysen-sp-selfcare:latest` — config mount `/opt/rysen-sp-selfcare/proxy.cfg`

## Update stack (ipsc branch — rebuild rysen/monitor, pull proxies)

Does **not** overwrite `/etc/rysen/*.cfg` or `mysql/`:

```bash
cd /opt/rysen-src && git pull
cp docker-configs/docker-compose-stack.yml /etc/rysen/docker-compose.yml
# Edit compose if needed: MariaDB env, monitor path — keep your existing secrets

cd /etc/rysen
docker compose down
docker compose pull ipsc-proxy proxy
docker compose build rysen monitor
docker compose up -d
conntrack -F
docker compose ps
```

Remove stale images only (optional, does not touch `/etc/rysen`):

```bash
docker rmi rysen-local:latest 2>/dev/null || true
docker image prune -f
```

## Update rysen only (minimal compose — no monitor)

```bash
cd /opt/rysen-src && git pull
cp docker-configs/docker-compose.yml /etc/rysen/docker-compose.yml
cd /etc/rysen
docker compose build rysen
docker compose pull ipsc-proxy
docker compose up -d rysen ipsc-proxy
conntrack -F
```

If `ipsc-proxy` is missing from `docker compose config --services`, check for a stale `compose.yaml` in `/etc/rysen` that overrides `docker-compose.yml`.

## Restart the container (for example when config is changed)

`docker compose restart`

## After rysen is updated or restarted this may be required

`conntrack -F`

This flushes the connection tracking table for NAT. Without this, you might not see traffic for a while.

For more docker commands go [here](Docker%20Commands%20Cheat%20Sheet)

## After merge to master (RYSEN v1.5.0)

When soak testing is complete and `ipsc` is merged to **master** as **version 1.5.0**:

1. Set `version.txt` to `1.5.0` and finalise [CHANGELOG.md](../CHANGELOG.md).
2. In `docker-compose_install.sh`, set `RYSEN_REPO_BASE` and `RYSEN_GIT_BRANCH` to `master`, replace git clone + `docker compose build` with `docker compose pull`.
3. In `docker-compose.yml`, replace the `rysen` `build:` block with `image: shaymez/rysen:latest`.
4. Push to master — GitHub Actions publishes `shaymez/rysen:latest` with IPSC + selfcare included.
5. Pair with [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) **v1.5.0** for dashboard and selfcare UI.

## Postrequisites

### RYSEN Master+ Suite (SYSTEMX)
You can however add the RYSEN Master+ suite, Dashboard, Whiptail menus and additional software to make a fully fletched DMR Master Server if you wish by executing our official installer [here](https://github.com/shaymez/RYSEN-Installer)

*Credits:*
Simon G7RZU (original installer),
Shane Daley M0VUB (Compose V2 refactor, IPSC docker updates)
