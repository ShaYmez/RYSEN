###############################################################################
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   Version traceability: install identity, report-socket server info, API ping.
###############################################################################

import hashlib
import json
import logging
import os
import socket
import ssl
import urllib.error
import urllib.request
from time import time

from rysen_version import __version__, user_agent

logger = logging.getLogger('HBlink')

PING_URL = 'https://api.freestar.network/v1/rysen/ping.php'
REPORT_PROTOCOL = 1
_PING_INTERVAL_S = 86400


def install_id_path(log_dir='/opt/rysen/log'):
    return os.path.join(log_dir, '.install_id')


def _hash_seed(seed):
    return hashlib.sha256(seed.encode('utf-8', errors='ignore')).hexdigest()


def get_install_id(log_dir='/opt/rysen/log'):
    path = install_id_path(log_dir)
    try:
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as fh:
                value = fh.read().strip()
                if value:
                    return value
    except OSError:
        pass

    seed = '{}:{}'.format(socket.gethostname(), os.getpid())
    for candidate in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            if os.path.isfile(candidate):
                with open(candidate, encoding='utf-8') as fh:
                    machine_id = fh.read().strip()
                    if machine_id:
                        seed = machine_id
                        break
        except OSError:
            continue

    install_id = _hash_seed(seed)
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(install_id)
    except OSError as exc:
        logger.debug('(RYSEN) could not persist install_id: %s', exc)
    return install_id


def server_info_payload(hostname=None, started_at=None):
    if hostname is None:
        hostname = socket.gethostname()
    if started_at is None:
        started_at = int(time())
    return {
        'rysen_version': __version__,
        'report_protocol': REPORT_PROTOCOL,
        'hostname': hostname,
        'started_at': started_at,
    }


def server_info_message():
    body = json.dumps(server_info_payload(), separators=(',', ':')).encode('utf-8')
    return body


def post_version_ping(log_dir='/opt/rysen/log'):
    payload = json.dumps({
        'version': __version__,
        'install_id': get_install_id(log_dir),
        'started_at': int(time()),
    }).encode('utf-8')
    request = urllib.request.Request(
        PING_URL,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': user_agent(),
        },
        method='POST',
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        response.read()


def ping_version_async(log_dir='/opt/rysen/log'):
    try:
        post_version_ping(log_dir)
        logger.debug('(RYSEN) version ping sent')
    except Exception as exc:
        logger.debug('(RYSEN) version ping failed: %s', exc)


def schedule_version_ping(reactor, log_dir='/opt/rysen/log'):
    from twisted.internet import threads

    reactor.callWhenRunning(lambda: threads.deferToThread(ping_version_async, log_dir))
    task = reactor.callLater(_PING_INTERVAL_S, schedule_version_ping, reactor, log_dir)
    return task
