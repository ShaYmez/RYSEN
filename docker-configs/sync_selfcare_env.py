#!/usr/bin/env python3
"""Generate docker compose .env from rysen.cfg [SELF SERVICE].

rysen reads DB credentials from rysen.cfg at runtime; mariadb and the selfcare
proxy need the same values via compose environment. This script keeps them aligned
without hand-editing docker-compose.yml on upgrade.
"""
from __future__ import annotations

import argparse
import configparser
import os
import sys


def _read_self_service(rysen_cfg: str) -> dict[str, str]:
    cp = configparser.ConfigParser()
    if not cp.read(rysen_cfg):
        sys.exit(f'Cannot read {rysen_cfg}')
    if 'SELF SERVICE' not in cp:
        sys.exit(f'No [SELF SERVICE] section in {rysen_cfg}')
    ss = cp['SELF SERVICE']
    return {
        'DB_HOST': ss.get('DB_HOST', 'mariadb'),
        'DB_PORT': str(ss.getint('DB_PORT', 3306)),
        'DB_USER': ss.get('DB_USER', 'selfcare'),
        'DB_PASS': ss.get('DB_PASS', ''),
        'DB_NAME': ss.get('DB_NAME', 'selfcare'),
        'DB_ROOT_PASS': ss.get('DB_ROOT_PASS', ''),
    }


def _read_existing_root(env_file: str) -> str | None:
    if not os.path.isfile(env_file):
        return None
    with open(env_file, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('MYSQL_ROOT_PASSWORD='):
                return line.split('=', 1)[1]
    return None


def _write_env(data: dict[str, str], env_file: str, root_password: str) -> None:
    env_dir = os.path.dirname(env_file)
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
    lines = [
        '# Generated from rysen.cfg [SELF SERVICE] — re-run sync-selfcare-env.sh after DB changes',
        f"DB_HOST={data['DB_HOST']}",
        f"DB_PORT={data['DB_PORT']}",
        f"DB_USER={data['DB_USER']}",
        f"DB_PASS={data['DB_PASS']}",
        f"DB_NAME={data['DB_NAME']}",
        f"MYSQL_DATABASE={data['DB_NAME']}",
        f"MYSQL_USER={data['DB_USER']}",
        f"MYSQL_PASSWORD={data['DB_PASS']}",
        f"MYSQL_ROOT_PASSWORD={root_password}",
    ]
    with open(env_file, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.chmod(env_file, 0o600)


def _sync_proxy_cfg(data: dict[str, str], proxy_cfg: str) -> None:
    if not os.path.isfile(proxy_cfg):
        print(f'proxy.cfg not found ({proxy_cfg}); skipping proxy [SELF SERVICE] sync')
        return
    cp = configparser.ConfigParser()
    cp.read(proxy_cfg)
    if 'SELF SERVICE' not in cp:
        cp.add_section('SELF SERVICE')
    ss = cp['SELF SERVICE']
    ss['use_selfservice'] = 'True'
    ss['server'] = data['DB_HOST']
    ss['username'] = data['DB_USER']
    ss['password'] = data['DB_PASS']
    ss['db_name'] = data['DB_NAME']
    ss['port'] = data['DB_PORT']
    with open(proxy_cfg, 'w', encoding='utf-8') as fh:
        cp.write(fh)
    print(f'Updated {proxy_cfg} [SELF SERVICE] from rysen.cfg')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rysen-cfg', default='/etc/rysen/rysen.cfg')
    parser.add_argument('--env-file', default='/etc/rysen/.env')
    parser.add_argument('--proxy-cfg', default='/etc/rysen/proxy.cfg')
    parser.add_argument('--no-proxy', action='store_true',
                        help='Do not update proxy.cfg [SELF SERVICE]')
    args = parser.parse_args()

    data = _read_self_service(args.rysen_cfg)
    if not data['DB_PASS']:
        sys.exit('DB_PASS is empty in rysen.cfg [SELF SERVICE]')

    mysql_data_dir = os.path.join(os.path.dirname(args.rysen_cfg), 'mysql')
    existing_root = _read_existing_root(args.env_file)
    if data['DB_ROOT_PASS']:
        root_password = data['DB_ROOT_PASS']
    elif existing_root and os.path.isdir(mysql_data_dir):
        root_password = existing_root
    else:
        root_password = data['DB_PASS']

    _write_env(data, args.env_file, root_password)
    print(f'Wrote {args.env_file} from {args.rysen_cfg} [SELF SERVICE]')

    if not args.no_proxy:
        _sync_proxy_cfg(data, args.proxy_cfg)


if __name__ == '__main__':
    main()
