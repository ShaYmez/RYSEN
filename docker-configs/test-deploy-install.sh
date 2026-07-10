#!/bin/bash
#
###############################################################################
# RYSEN test / dev stack installer
#
# Builds RYSEN from a git branch (default: unit2), pulls satellite proxy images
# from Docker Hub, optional MariaDB + hotspot selfcare proxy + monitor.
#
# Proxies (ipsc-proxy, selfcare proxy) are NOT built from source — unit-to-unit
# voice is handled in bridge_master; Hub images are sufficient for Phase 4A.
#
# Non-interactive example:
#   curl -fsSL .../test-deploy-install.sh | bash
#   RYSEN_BRANCH=unit2 INSTALL_MONITOR=yes MONITOR_BRANCH=master bash test-deploy-install.sh
#
# Environment (all optional):
#   RYSEN_BRANCH      Git branch to build (default: unit2, or prompt)
#   RYSEN_SRC         Clone path (default: /opt/rysen-src)
#   RYSEN_IMAGE       Local image tag (default: rysen-local:latest)
#   INSTALL_SELFCARE   yes|no — hotspot proxy + MariaDB (default: yes, or prompt)
#   INSTALL_MONITOR    yes|no — dashboard (default: prompt; requires selfcare)
#   MONITOR_BRANCH    Git branch for monitor (default: master, or prompt)
#   MONITOR_SRC       Clone path (default: /opt/rysen-monitor-src)
#   MONITOR_IMAGE     Image tag (default: rysen-monitor-local:latest or Hub)
#   DB_ROOT           MariaDB root password (default: prompt or random)
#   DB_PASS           selfcare user password (default: prompt or random)
#   SKIP_DOCKER       1 = skip Docker CE install if docker already present
#   NONINTERACTIVE    1 = no prompts; use defaults / env only
###############################################################################
#
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#   GPL-3.0 — same as RYSEN
###############################################################################

set -euo pipefail

RYSEN_GITHUB="${RYSEN_GITHUB:-ShaYmez/RYSEN}"
MONITOR_GITHUB="${MONITOR_GITHUB:-ShaYmez/RYSEN-MONITOR}"
RYSEN_BRANCH="${RYSEN_BRANCH:-}"
RYSEN_SRC="${RYSEN_SRC:-/opt/rysen-src}"
RYSEN_IMAGE="${RYSEN_IMAGE:-rysen-local:latest}"
INSTALL_SELFCARE="${INSTALL_SELFCARE:-}"
INSTALL_MONITOR="${INSTALL_MONITOR:-}"
MONITOR_BRANCH="${MONITOR_BRANCH:-}"
MONITOR_SRC="${MONITOR_SRC:-/opt/rysen-monitor-src}"
MONITOR_IMAGE="${MONITOR_IMAGE:-}"
DB_ROOT="${DB_ROOT:-}"
DB_PASS="${DB_PASS:-}"
SKIP_DOCKER="${SKIP_DOCKER:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

RYSEN_ETC="/etc/rysen"

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \?//'
    exit 0
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

prompt() {
    local var_name="$1"
    local question="$2"
    local default="$3"
    if [[ "$NONINTERACTIVE" == "1" ]]; then
        printf -v "$var_name" '%s' "$default"
        return
    fi
    local reply
    read -r -p "${question} [${default}]: " reply
    if [[ -z "$reply" ]]; then
        printf -v "$var_name" '%s' "$default"
    else
        printf -v "$var_name" '%s' "$reply"
    fi
}

prompt_yn() {
    local var_name="$1"
    local question="$2"
    local default="$3"
    if [[ "$NONINTERACTIVE" == "1" ]]; then
        printf -v "$var_name" '%s' "$default"
        return
    fi
    local reply
    while true; do
        read -r -p "${question} (y/n) [${default}]: " reply
        reply="${reply:-$default}"
        case "${reply,,}" in
            y|yes) printf -v "$var_name" '%s' "yes"; return ;;
            n|no)  printf -v "$var_name" '%s' "no"; return ;;
        esac
        echo "Please answer y or n."
    done
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "This installer must be run as root."
    exit 1
fi

prompt RYSEN_BRANCH "RYSEN git branch to build" "${RYSEN_BRANCH:-unit2}"
prompt_yn INSTALL_SELFCARE "Install hotspot selfcare proxy + MariaDB" "${INSTALL_SELFCARE:-yes}"

if [[ "$INSTALL_SELFCARE" == "yes" ]]; then
    prompt_yn INSTALL_MONITOR "Install RYSEN-MONITOR dashboard" "${INSTALL_MONITOR:-yes}"
else
    INSTALL_MONITOR="no"
fi

if [[ "$INSTALL_MONITOR" == "yes" ]]; then
    prompt MONITOR_BRANCH "RYSEN-MONITOR git branch" "${MONITOR_BRANCH:-master}"
fi

if [[ -z "$DB_PASS" && "$INSTALL_SELFCARE" == "yes" ]]; then
    _db_default="$(openssl rand -hex 8 2>/dev/null || echo changeme-selfcare)"
    prompt DB_PASS "MariaDB selfcare password" "$_db_default"
fi
if [[ -z "$DB_ROOT" && "$INSTALL_SELFCARE" == "yes" ]]; then
    _root_default="$(openssl rand -hex 8 2>/dev/null || echo changeme-root)"
    prompt DB_ROOT "MariaDB root password" "$_root_default"
fi
if [[ "$INSTALL_SELFCARE" != "yes" ]]; then
    DB_PASS="${DB_PASS:-unused}"
    DB_ROOT="${DB_ROOT:-unused}"
fi

RYSEN_REPO_BASE="https://raw.githubusercontent.com/${RYSEN_GITHUB}/refs/heads/${RYSEN_BRANCH}"
MONITOR_REPO_BASE="https://raw.githubusercontent.com/${MONITOR_GITHUB}/refs/heads/${MONITOR_BRANCH:-master}"

clear
echo "RYSEN test-deploy installer"
echo "  RYSEN branch:  ${RYSEN_BRANCH} → ${RYSEN_IMAGE}"
echo "  Selfcare:      ${INSTALL_SELFCARE} (proxy + MariaDB)"
echo "  Monitor:       ${INSTALL_MONITOR}"
if [[ "$INSTALL_MONITOR" == "yes" ]]; then
    echo "  Monitor branch: ${MONITOR_BRANCH}"
fi
echo "  Proxies:       Hub images (ipsc-proxy + selfcare proxy)"
echo
sleep 2

install_docker() {
    echo "Installing Docker CE + Compose plugin..."
    apt-get -y remove docker docker-engine docker.io 2>/dev/null || true
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
    apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

if [[ "$SKIP_DOCKER" != "1" ]] || ! command -v docker >/dev/null 2>&1; then
    if command -v docker >/dev/null 2>&1; then
        echo "Docker already installed — skipping CE install (set SKIP_DOCKER=1 to silence)."
        apt-get -y install git ca-certificates curl 2>/dev/null || true
    else
        install_docker
    fi
fi

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
systemctl enable docker 2>/dev/null || true
systemctl restart docker

mkdir -p "${RYSEN_ETC}/json" "${RYSEN_ETC}/mysql" /var/log/rysen /var/log/rysen-monitor
chmod -R 755 "${RYSEN_ETC}" /var/log/rysen /var/log/rysen-monitor

echo "Fetching config from ${RYSEN_GITHUB}@${RYSEN_BRANCH}..."
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/rysen.cfg" -o "${RYSEN_ETC}/rysen.cfg"
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/rules.py" -o "${RYSEN_ETC}/rules.py"
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/ipsc-proxy-SAMPLE.cfg" -o "${RYSEN_ETC}/ipsc-proxy.cfg"
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/config/proxy-SAMPLE.cfg" -o "${RYSEN_ETC}/proxy.cfg"
curl -fsSL "${RYSEN_REPO_BASE}/docker-configs/docker-compose-stack.yml" -o "${RYSEN_ETC}/docker-compose.yml"

if [[ "$INSTALL_MONITOR" == "yes" && ! -f "${RYSEN_ETC}/fdmr-mon.cfg" ]]; then
    echo "Fetching monitor config sample..."
    if curl -fsSL "${MONITOR_REPO_BASE}/fdmr-mon-SAMPLE.cfg" -o "${RYSEN_ETC}/fdmr-mon.cfg"; then
        :
    elif curl -fsSL "${MONITOR_REPO_BASE}/fdmr-mon.cfg" -o "${RYSEN_ETC}/fdmr-mon.cfg"; then
        :
    else
        echo "Warning: could not download fdmr-mon.cfg — add ${RYSEN_ETC}/fdmr-mon.cfg before starting monitor."
    fi
fi

echo "Building RYSEN from branch ${RYSEN_BRANCH}..."
if [[ -d "${RYSEN_SRC}/.git" ]]; then
    git -C "${RYSEN_SRC}" fetch origin
    git -C "${RYSEN_SRC}" checkout "${RYSEN_BRANCH}"
    git -C "${RYSEN_SRC}" pull --ff-only origin "${RYSEN_BRANCH}" || true
else
    git clone --branch "${RYSEN_BRANCH}" --depth 1 "https://github.com/${RYSEN_GITHUB}.git" "${RYSEN_SRC}"
fi
docker build -t "${RYSEN_IMAGE}" "${RYSEN_SRC}"

sed -i "s|shaymez/rysen:latest|${RYSEN_IMAGE}|g" "${RYSEN_ETC}/docker-compose.yml"

if [[ "$INSTALL_SELFCARE" == "yes" ]]; then
    sed -i "s|CHANGE_ME_ROOT|${DB_ROOT}|g" "${RYSEN_ETC}/docker-compose.yml"
    sed -i "s|CHANGE_ME_SELFCARE|${DB_PASS}|g" "${RYSEN_ETC}/docker-compose.yml"
fi

MONITOR_USE_HUB="no"
if [[ "$INSTALL_MONITOR" == "yes" ]]; then
    if [[ "${MONITOR_BRANCH}" == "master" && -z "${MONITOR_IMAGE:-}" ]]; then
        prompt_yn MONITOR_USE_HUB "Use Hub monitor image (shaymez/rysen-monitor:latest)" "yes"
    fi
    if [[ "$MONITOR_USE_HUB" == "yes" && -z "${MONITOR_IMAGE:-}" ]]; then
        MONITOR_IMAGE="shaymez/rysen-monitor:latest"
    else
        MONITOR_IMAGE="${MONITOR_IMAGE:-rysen-monitor-local:latest}"
        echo "Building RYSEN-MONITOR from branch ${MONITOR_BRANCH}..."
        if [[ -d "${MONITOR_SRC}/.git" ]]; then
            git -C "${MONITOR_SRC}" fetch origin
            git -C "${MONITOR_SRC}" checkout "${MONITOR_BRANCH}"
            git -C "${MONITOR_SRC}" pull --ff-only origin "${MONITOR_BRANCH}" || true
        else
            git clone --branch "${MONITOR_BRANCH}" --depth 1 "https://github.com/${MONITOR_GITHUB}.git" "${MONITOR_SRC}"
        fi
        docker build -t "${MONITOR_IMAGE}" "${MONITOR_SRC}"
    fi
    sed -i "s|shaymez/rysen-monitor:latest|${MONITOR_IMAGE}|g" "${RYSEN_ETC}/docker-compose.yml"
fi

cat > /etc/sysctl.d/99-rysen.conf <<'EOF'
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
EOF
sysctl --system >/dev/null 2>&1 || sysctl --system

mkdir -p /var/log/rysen
touch /var/log/rysen/rysen.log
chmod -R 755 /var/log/rysen
chown -R 54000:54000 /var/log/rysen 2>/dev/null || true
chown -R 54000:54000 "${RYSEN_ETC}" 2>/dev/null || true

cd "${RYSEN_ETC}"

echo "Pulling satellite proxy images from Docker Hub..."
if [[ "$INSTALL_SELFCARE" == "yes" ]]; then
    docker compose pull ipsc-proxy proxy mariadb
else
    docker compose pull ipsc-proxy
fi
if [[ "$INSTALL_MONITOR" == "yes" && "$MONITOR_USE_HUB" == "yes" ]]; then
    docker compose pull monitor
fi

echo "Starting stack..."
if [[ "$INSTALL_SELFCARE" == "yes" && "$INSTALL_MONITOR" == "yes" ]]; then
    docker compose up -d
elif [[ "$INSTALL_SELFCARE" == "yes" ]]; then
    docker compose up -d rysen ipsc-proxy mariadb proxy
elif [[ "$INSTALL_MONITOR" == "yes" ]]; then
    echo "Monitor requires selfcare/MariaDB — set INSTALL_SELFCARE=yes"
    exit 1
else
    docker compose up -d rysen ipsc-proxy
fi

conntrack -F 2>/dev/null || true

echo
echo "=== Deploy complete ==="
echo "RYSEN:      ${RYSEN_IMAGE} (branch ${RYSEN_BRANCH})"
echo "Config:     ${RYSEN_ETC}/rysen.cfg  — edit slots/peers, then: docker compose restart rysen"
if [[ "$INSTALL_SELFCARE" == "yes" ]]; then
    echo "DB pass:    selfcare=${DB_PASS}  (align rysen.cfg [SELF SERVICE] and proxy.cfg)"
fi
if [[ "$INSTALL_MONITOR" == "yes" ]]; then
    echo "Monitor:    http://$(hostname -I | awk '{print $1}'):9000"
fi
echo "Logs:       docker logs systemx -f"
echo "Unit test:  docker logs systemx 2>&1 | grep -iE 'UNIT voice|SUB_MAP|Reflector: Private'"
echo "Docs:       doc/unit-roadmap.md on branch ${RYSEN_BRANCH}"

docker compose ps
