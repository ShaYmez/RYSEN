#!/bin/bash
#
###############################################################################
#   Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
#   Further Developed (C) 2026 by Shane Daley, M0VUB <shane@freestar.network>
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################
#
# ipsc branch: clones source and builds the rysen image locally (no Docker Hub).
# MERGE to master: set RYSEN_GIT_BRANCH and RYSEN_REPO_BASE to master, remove
# git clone + docker compose build; use docker compose pull for rysen instead.

set -euo pipefail

# MERGE: change both to master
RYSEN_REPO_BASE="https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/ipsc"
RYSEN_GIT_BRANCH="ipsc"
RYSEN_GIT_REPO="https://github.com/ShaYmez/RYSEN.git"
RYSEN_SRC="/opt/rysen-src"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "This installer must be run as root."
    exit 1
fi

clear
echo "RYSEN Master+ Docker installer..."
sleep 3

echo "Installing required packages..."
echo "Install Docker Community Edition and Compose V2 plugin..."
apt-get -y remove docker docker-engine docker.io || true
apt-get -y update
apt-get -y install ca-certificates curl gnupg git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get -y update
apt-get -y install \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

echo "Set userland-proxy to false..."
mkdir -p /etc/docker
cat <<'EOF' > /etc/docker/daemon.json
{
    "userland-proxy": false,
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "10m",
        "max-file": "3"
    }
}
EOF

echo "Restart docker..."
systemctl enable docker
systemctl restart docker

echo "Make config directory..."
mkdir -p /etc/rysen/acme.sh
mkdir -p /etc/rysen/certs
mkdir -p /etc/rysen/json

echo "Get config /etc/rysen/rysen.cfg ..."
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/rysen.cfg" -o /etc/rysen/rysen.cfg

echo "Get rules /etc/rysen/rules.py..."
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/rules.py" -o /etc/rysen/rules.py

echo "Get ipsc-proxy.cfg ..."
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/ipsc-proxy-SAMPLE.cfg" -o /etc/rysen/ipsc-proxy.cfg

echo "Get proxy.cfg (optional hotspot profile)..."
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/proxy-SAMPLE.cfg" -o /etc/rysen/proxy.cfg

echo "Clone RYSEN source (${RYSEN_GIT_BRANCH}) for local image build..."
rm -rf "${RYSEN_SRC}"
git clone -b "${RYSEN_GIT_BRANCH}" --depth 1 "${RYSEN_GIT_REPO}" "${RYSEN_SRC}"

echo "Install compose file from clone..."
cp "${RYSEN_SRC}/docker-configs/docker-compose.yml" /etc/rysen/docker-compose.yml

echo "Set perms on config directory..."
chmod -R 755 /etc/rysen
chown -R 54000:54000 /etc/rysen

echo "Tune network stack..."
cat <<'EOF' > /etc/sysctl.d/99-rysen.conf
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
EOF

sysctl --system

echo "Create log directory..."
mkdir -p /var/log/rysen
touch /var/log/rysen/rysen.log
chmod -R 755 /var/log/rysen
chown -R 54000:54000 /var/log/rysen

echo "Build and start RYSEN + IPSC proxy (hotspot proxy optional via --profile hotspot)..."
cd /etc/rysen
docker compose build rysen ipsc-proxy
docker compose up -d rysen ipsc-proxy
docker container logs systemx
docker container logs ipsc-proxy

echo "Check out docs @ https://github.com/ShaYmez/RYSEN for extra functionality."
echo "IPSC Phase 2a: proxy on UDP 56002, backends IPSC-0..199 on 56003-56202."
echo "CPS Master port = 56002. See doc/ipsc-phase1.md and ipsc-proxy.cfg."
echo "To enable hotspot proxy: docker compose --profile hotspot up -d"
echo "To rebuild after git pull: cd ${RYSEN_SRC} && git pull && cd /etc/rysen && docker compose build rysen && docker compose up -d rysen"
echo "Setup complete!"
