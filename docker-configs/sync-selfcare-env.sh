#!/bin/bash
# Generate /etc/rysen/.env from rysen.cfg [SELF SERVICE] for docker compose.
set -euo pipefail

RYSEN_CFG="${RYSEN_CFG:-/etc/rysen/rysen.cfg}"
ENV_FILE="${ENV_FILE:-/etc/rysen/.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${RYSEN_CFG}" ]]; then
    echo "Missing ${RYSEN_CFG}" >&2
    exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "${SCRIPT_DIR}/sync_selfcare_env.py" \
        --rysen-cfg "${RYSEN_CFG}" \
        --env-file "${ENV_FILE}" \
        "$@"
fi

echo "python3 is required to sync selfcare env from rysen.cfg" >&2
exit 1
