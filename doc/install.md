# \*\*The current recommended way of installing RYSEN Master+ is in docker. \*\*

Docker is the fastest way to having a running system, complete with proxy and echo, ready to serve repeaters and hotspots. The Docker image can be run on any system that can run Linux docker containers. We recommend Debian 11.

Not convinced? Read [Why Docker?](https://github.com/ShaYmez/RYSEN/blob/master/doc/why-docker.md)

# Quick Start

For a one-shot install on a Debian or Debian-based system, paste this into the terminal of a running debian-like Linux system. This will install RYSEN Master+ only. For the RYSEN Master+ Suite scroll down to the bottom.

*Important!*
Must be root!! for this install to work correctly! Sudo is for amateurs!

`curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/docker-compose_install.sh | bash`

This works on Debian 10, 11, 12, PiOs (Raspbian) and recent Ubuntu systems, on other flavours, you may need to follow the process below.

The installer uses Docker Compose V2 (`docker compose`). On **master** the installer pulls **`shaymez/rysen:latest`** and satellite proxy images from Docker Hub.

The rest of this page details the manual install process.

## Prerequisites

Firstly, you need a system running docker with the Compose V2 plugin. Follow the instructions for your distro [here](https://docs.docker.com/engine/install/debian/).

Install the recommended packages:

`apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`

A change needs to be made to the docker config for openbridge to work correctly:

`echo '{ "userland-proxy": false}' > /etc/docker/daemon.json`

`systemctl restart docker`

## Grab and edit the config file

Get the file: [rysen.cfg](https://github.com/ShaYmez/RYSEN/blob/master/docker-configs/config/rysen.cfg)

`mkdir /etc/rysen`

place the rysen.cfg file in this directory.

For IPSC, the docker install starts **rysen** and **ipsc-proxy** (public UDP **56002** CPS Master port; backends `IPSC-0`…`IPSC-199` on `56003`–`56202`). Enable IPSC auth in CPS and match `AUTH_KEY` in `rysen.cfg`. IPSC repeater selfcare and the dashboard require [RYSEN-MONITOR](https://github.com/ShaYmez/RYSEN-MONITOR) (v1.5.0+) and MariaDB — see monitor repo docs; minimal compose here is RYSEN + proxy only. See [ipsc-phase1.md](ipsc-phase1.md), [ipsc-roadmap.md](ipsc-roadmap.md), [IPSC-SAMPLE.cfg](../docker-configs/config/IPSC-SAMPLE.cfg), and [ipsc-proxy-SAMPLE.cfg](../docker-configs/config/ipsc-proxy-SAMPLE.cfg).

## Make rules file

`echo "BRIDGES = {'9990': [{'SYSTEM': 'ECHO', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/rysen/rules.py`

## Make JSON directory

`mkdir -p /etc/rysen/json`

## Make sure docker container can access config and rules ##

`chmod 755 /etc/rysen -R`

## create /etc/rysen/docker-compose.yml

Download the file here: [docker-compose.yml](https://github.com/ShaYmez/RYSEN/raw/master/docker-configs/docker-compose.yml)

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

`docker compose pull rysen ipsc-proxy`

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

## Selfcare database credentials (full stack)

The production stack template ([docker-compose-stack.yml](../docker-configs/docker-compose-stack.yml)) ships with **`CHANGE_ME_*` placeholders** for MariaDB. You must set these manually to match your deployment. There is no automatic sync from `rysen.cfg` — keep the values aligned yourself when you install or change passwords.

### Where credentials live

| File | Read by | Relevant keys |
|------|---------|---------------|
| `/etc/rysen/rysen.cfg` `[SELF SERVICE]` | **rysen** (IPSC repeater selfcare) | `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME`, `ENABLED` |
| `/etc/rysen/proxy.cfg` `[SELF SERVICE]` | **proxy** (hotspot selfcare) | `server`, `port`, `username`, `password`, `db_name`, `use_selfservice` |
| `/etc/rysen/docker-compose.yml` | **mariadb** + **proxy** containers | `MYSQL_*` and `DB_*` environment variables |

On the compose network, `DB_HOST` / `server` should be **`mariadb`** (the compose service name), not `127.0.0.1`.

### Compose placeholders to edit

After copying the stack template to `/etc/rysen/docker-compose.yml`, set:

| Placeholder in compose | Set to |
|------------------------|--------|
| `CHANGE_ME_SELFCARE` | Same as `DB_PASS` in `rysen.cfg` and `password` in `proxy.cfg` (mariadb `MYSQL_PASSWORD` and proxy `DB_PASS`) |
| `CHANGE_ME_ROOT` | MariaDB root password used when `/etc/rysen/mysql` was first created |

Also confirm `MYSQL_DATABASE` / `MYSQL_USER` / `DB_NAME` / `DB_USER` match `DB_NAME` and `DB_USER` in `rysen.cfg` (default: `selfcare`).

### Enable selfcare

In `rysen.cfg`:

```
[SELF SERVICE]
ENABLED: True
```

In `proxy.cfg` (if using the selfcare hotspot proxy):

```
[SELF SERVICE]
USE_SELFSERVICE = True
```

### Existing MariaDB data

If `/etc/rysen/mysql` already exists from a prior install, the database users and passwords are **already defined** in that volume. Set compose env vars to those existing values — do not change `MYSQL_*` to something new unless you intend to re-init MariaDB (which would wipe selfcare data).

`MYSQL_ROOT_PASSWORD` only applies on **first** container init. On an existing volume it should still match the root password from the original install so the linuxserver MariaDB image behaves correctly.

### Upgrading the stack compose file

Downloading a fresh `docker-compose-stack.yml` **overwrites** `/etc/rysen/docker-compose.yml` and resets DB placeholders. Either:

1. Edit the placeholders again after download, or  
2. Copy your `mariadb` / `proxy` `environment:` block from the old compose file before replacing it.

This does **not** modify `rysen.cfg`, `proxy.cfg`, or `mysql/` — only the compose file.

## Update stack (master — pull all images)

Does **not** overwrite `/etc/rysen/*.cfg` or `mysql/`:

```bash
curl -fsSL https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/docker-compose-stack.yml \
  -o /etc/rysen/docker-compose.yml
# Re-apply DB credentials — see "Selfcare database credentials" above

cd /etc/rysen
docker compose down
docker compose pull
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
curl -fsSL https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/docker-compose.yml -o /etc/rysen/docker-compose.yml
cd /etc/rysen
docker compose pull rysen ipsc-proxy
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

## Postrequisites

### RYSEN Master+ Suite (SYSTEMX)
You can however add the RYSEN Master+ suite, Dashboard, Whiptail menus and additional software to make a fully fletched DMR Master Server if you wish by executing our official installer [here](https://github.com/shaymez/RYSEN-Installer)

*Credits:*
Simon G7RZU (original installer),
Shane Daley M0VUB (Compose V2 refactor, IPSC docker updates)
