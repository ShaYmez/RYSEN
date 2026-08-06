###############################################################################
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   Single source of truth for the RYSEN release version.
###############################################################################

import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'version.txt')

STOCK_PACKAGE_IDS = frozenset({
    'SYSTEM-X',
    'MMDVM_SYSTEM-X',
    'MMDVM_RYSEN',
    'MMDVM_HBlink',
    'MMDVM_HBLINK',
    '',
})

USER_AGENT_PREFIX = 'RYSEN'
GITHUB_URL = 'https://github.com/ShaYmez/RYSEN'


def read_version_file(path=_VERSION_FILE):
    with open(path, encoding='utf-8') as fh:
        return fh.read().strip()


__version__ = read_version_file()


def user_agent():
    return '{}/{}'.format(USER_AGENT_PREFIX, __version__)


def decode_package_id(value):
    if value is None:
        return ''
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', errors='ignore').rstrip('\x00').strip()
    return str(value).strip()


def advertised_package_id(config_value):
    """Return 40-char HBP PACKAGE_ID bytes; stock values become RYSEN-x.y.z."""
    pkg = decode_package_id(config_value)
    if pkg in STOCK_PACKAGE_IDS:
        pkg = 'RYSEN-{}'.format(__version__)
    return pkg.ljust(40)[:40].encode('utf-8')


def advertised_package_id_bytes(config_value):
    value = advertised_package_id(config_value)
    if isinstance(value, bytes):
        return value
    return value.encode('utf-8')
