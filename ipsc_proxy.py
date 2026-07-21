#!/usr/bin/env python3
###############################################################################
#   Motorola IPSC UDP proxy — single public port to many backend IPSC masters
#   Copyright (C) 2026 Shane Daley, M0VUB <shane@freestar.network>
#
#   Modelled on hotspot_proxy_v2.py (Simon G7RZU). Routes by repeater radio ID
#   (bytes 1:5) and by backend source port for master replies without peer ID.
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
###############################################################################

import argparse
import configparser
import ipaddress
import json
import os
import random
import signal
import sys
from datetime import datetime
from time import time

from setproctitle import setproctitle
from twisted.internet import reactor, task
from twisted.internet.protocol import DatagramProtocol

from dmr_utils3.utils import int_id
from ipsc_const import PRCL, PRIN, peer_id_from_packet

__author__ = 'Shane Daley M0VUB'
__version__ = '1.5.1'


def is_ipv4_address(ip):
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError:
        return False


class IpscProxy(DatagramProtocol):

    def __init__(self, master, conn_track, peer_track, black_list, ip_black_list,
                 timeout, debug, client_info):
        self.master = master
        self.conn_track = conn_track
        self.peer_track = peer_track
        self.timeout = timeout
        self.debug = debug
        self.client_info = client_info
        self.black_list = black_list
        self.ip_black_list = ip_black_list

    def cleanup_peer(self, peer_id):
        peer = self.peer_track.get(peer_id)
        if not peer:
            return
        timer = peer.get('timer')
        if timer:
            try:
                if timer.active():
                    timer.cancel()
            except Exception:
                pass
        self.reaper(peer_id)

    def reaper(self, peer_id):
        peer = self.peer_track.get(peer_id)
        if not peer:
            return
        if self.debug:
            print('dead', peer_id)
        if self.client_info and peer_id != b'\xff\xff\xff\xff':
            print(
                f"{datetime.now().replace(microsecond=0)} Client: "
                f"ID:{str(int_id(peer_id)).rjust(9)} IP:{peer['shost'].rjust(15)} "
                f"Port:{peer['sport']} Removed."
            )
        dport = peer.get('dport')
        if dport in self.conn_track:
            self.transport.write(PRCL + peer_id, (self.master, dport))
            self.conn_track[dport] = False
        if peer_id in self.peer_track:
            del self.peer_track[peer_id]

    def _route_master_reply(self, data, master_src_port):
        peer_id = peer_id_from_packet(data)
        if not peer_id or peer_id not in self.peer_track:
            peer_id = self.conn_track.get(master_src_port)
        if peer_id and peer_id in self.peer_track:
            peer = self.peer_track[peer_id]
            self.transport.write(data, (peer['shost'], peer['sport']))
            return True
        return False

    def datagramReceived(self, data, addr):
        host, port = addr

        if host in self.ip_black_list:
            return

        if host == self.master:
            if self.debug:
                print(data)
            self._route_master_reply(data, port)
            return

        peer_id = peer_id_from_packet(data)
        if not peer_id:
            return

        if peer_id in self.peer_track:
            dport = self.peer_track[peer_id]['dport']
            self.peer_track[peer_id]['sport'] = port
            self.peer_track[peer_id]['shost'] = host
            self.transport.write(data, (self.master, dport))
            self.peer_track[peer_id]['timer'].reset(self.timeout)
            if self.debug:
                print(data)
            return

        if int_id(peer_id) in self.black_list:
            return

        ports_avail = [p for p in self.conn_track if not self.conn_track[p]]
        if not ports_avail:
            return

        dport = random.choice(ports_avail)
        self.conn_track[dport] = peer_id
        self.peer_track[peer_id] = {
            'dport': dport,
            'sport': port,
            'shost': host,
            'timer': reactor.callLater(self.timeout, self.reaper, peer_id),
        }
        self.transport.write(data, (self.master, dport))
        pripacket = b''.join([PRIN, host.encode('UTF-8'), b':', str(port).encode('UTF-8')])
        self.transport.write(pripacket, (self.master, dport))

        if self.client_info and peer_id != b'\xff\xff\xff\xff':
            print(
                f'{datetime.now().replace(microsecond=0)} New client: '
                f'ID:{str(int_id(peer_id)).rjust(9)} IP:{host.rjust(15)} Port:{port}, '
                f'assigned to port:{dport}.'
            )
        if self.debug:
            print(data)


def _load_config(config_file):
    config = configparser.ConfigParser()
    if not config.read(config_file):
        raise SystemExit(f"Configuration file '{config_file}' is not valid!")

    try:
        return {
            'master': config.get('IPSC_PROXY', 'MASTER'),
            'listen_port': config.getint('IPSC_PROXY', 'LISTENPORT'),
            'listen_ip': config.get('IPSC_PROXY', 'LISTENIP'),
            'dest_port_start': config.getint('IPSC_PROXY', 'DESTPORTSTART'),
            'dest_port_end': config.getint('IPSC_PROXY', 'DESTPORTEND'),
            'timeout': config.getint('IPSC_PROXY', 'TIMEOUT'),
            'stats': config.getboolean('IPSC_PROXY', 'STATS'),
            'debug': config.getboolean('IPSC_PROXY', 'DEBUG'),
            'client_info': config.getboolean('IPSC_PROXY', 'CLIENTINFO'),
            'black_list': json.loads(config.get('IPSC_PROXY', 'BLACKLIST')),
            'ip_black_list': json.loads(config.get('IPSC_PROXY', 'IPBLACKLIST')),
        }
    except (configparser.Error, json.JSONDecodeError, ValueError) as err:
        raise SystemExit(f'Error processing configuration file -- {err}') from err


if __name__ == '__main__':
    setproctitle(os.path.basename(__file__))
    print('(IPSC) Copyright (c) 2026 Shane Daley, M0VUB <shane@freestar.network>')
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    parser = argparse.ArgumentParser(description='Motorola IPSC UDP proxy')
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE',
                        help='Full path to ipsc-proxy.cfg')
    cli_args = parser.parse_args()

    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            'ipsc-proxy.cfg')

    cfg = _load_config(cli_args.CONFIG_FILE)

    conn_track = {}
    peer_track = {}

    for p in range(cfg['dest_port_start'], cfg['dest_port_end'] + 1):
        conn_track[p] = False

    listen_ip = cfg['listen_ip']
    master = cfg['master']

    if listen_ip == '' and os.environ.get('FDPROXY_IPV6'):
        listen_ip = '::'
    if listen_ip == '::' and is_ipv4_address(master):
        master = '::ffff:' + master

    if 'FDPROXY_CLIENTINFO' in os.environ:
        cfg['client_info'] = bool(os.environ['FDPROXY_CLIENTINFO'])
    if 'FDPROXY_LISTENPORT' in os.environ:
        cfg['listen_port'] = int(os.environ['FDPROXY_LISTENPORT'])

    def sig_handler(_signal, _frame):
        print(f'(GLOBAL) SHUTDOWN: IPSC PROXY TERMINATING WITH SIGNAL {_signal}')
        reactor.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, sig_handler)

    def looping_err_handle(failure):
        print('(GLOBAL) STOPPING REACTOR: Unhandled error in timed loop.\n', failure)
        reactor.stop()

    def stats():
        count = sum(1 for p in conn_track if conn_track[p])
        total = len(conn_track)
        print(f'{count} ports out of {total} in use ({total - count} free)')

    def blacklist_trimmer():
        now = time()
        for entry in list(cfg['ip_black_list']):
            expire = cfg['ip_black_list'][entry]
            if expire and expire < now:
                cfg['ip_black_list'].pop(entry)
                if cfg['client_info']:
                    print(f'Remove dynamic blacklist entry for {entry}')

    reactor.listenUDP(
        cfg['listen_port'],
        IpscProxy(
            master, conn_track, peer_track,
            cfg['black_list'], cfg['ip_black_list'],
            cfg['timeout'], cfg['debug'], cfg['client_info'],
        ),
        interface=listen_ip,
    )

    if cfg['stats']:
        stats_task = task.LoopingCall(stats)
        stats_a = stats_task.start(30)
        stats_a.addErrback(looping_err_handle)

    blacklist_task = task.LoopingCall(blacklist_trimmer)
    blacklist_a = blacklist_task.start(15)
    blacklist_a.addErrback(looping_err_handle)

    reactor.run()
