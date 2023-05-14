#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2018 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

'''
This module generates the configuration data structure for hblink.py and
assoicated programs that use it. It has been seaparated into a different
module so as to keep hblink.py easeier to navigate. This file only needs
updated if the items in the main configuraiton file (usually hblink.cfg)
change.
'''

import configparser
import sys
import const

import socket
import ipaddress 
from socket import gethostbyname
from languages import languages


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS'
__copyright__  = '(c) Simon Adlem, G7RZU 2020-2021, Copyright (c) 2016-2018 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem, G7RZU'
__email__      = 'simon@gb7fr.org.uk'

# Processing of ALS goes here. It's separated from the acl_build function because this
# code is hblink config-file format specific, and acl_build is abstracted
def process_acls(_config):
    # Global registration ACL
    _config['GLOBAL']['REG_ACL'] = acl_build(_config['GLOBAL']['REG_ACL'], const.PEER_MAX)

    # Global subscriber and TGID ACLs
    for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
        _config['GLOBAL'][acl] = acl_build(_config['GLOBAL'][acl], const.ID_MAX)

    # System level ACLs
    for system in _config['SYSTEMS']:
        # Registration ACLs (which make no sense for peer systems)
        if _config['SYSTEMS'][system]['MODE'] == 'MASTER':
            _config['SYSTEMS'][system]['REG_ACL'] = acl_build(_config['SYSTEMS'][system]['REG_ACL'], const.PEER_MAX)

        # Subscriber and TGID ACLs (valid for all system types)
        for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
            _config['SYSTEMS'][system][acl] = acl_build(_config['SYSTEMS'][system][acl], const.ID_MAX)

# Create an access control list that is programatically useable from human readable:
# ORIGINAL:  'DENY:1-5,3120101,3120124'
# PROCESSED: (False, set([(1, 5), (3120124, 3120124), (3120101, 3120101)]))
def acl_build(_acl, _max):
    if not _acl:
        return(True, set((const.ID_MIN, _max)))

    acl = [] #set()
    sections = _acl.split(':')

    if sections[0] == 'PERMIT':
        action = True
    else:
        action = False

    for entry in sections[1].split(','):
        if entry == 'ALL':
            acl.append((const.ID_MIN, _max))
            break

        elif '-' in entry:
            start,end = entry.split('-')
            start,end = int(start), int(end)
            if (const.ID_MIN <= start <= _max) or (const.ID_MIN <= end <= _max):
                acl.append((start, end))
            else:
                sys.exit('ACL CREATION ERROR, VALUE OUT OF RANGE ({} - {})IN RANGE-BASED ENTRY: {}'.format(const.ID_MIN, _max, entry))
        else:
            id = int(entry)
            if (const.ID_MIN <= id <= _max):
                acl.append((id, id))
            else:
                 sys.exit('ACL CREATION ERROR, VALUE OUT OF RANGE ({} - {}) IN SINGLE ID ENTRY: {}'.format(const.ID_MIN, _max, entry))

    return (action, acl)

def IsIPv4Address(ip):
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError as errorCode:
        pass
        return False
    
def IsIPv6Address(ip):
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ValueError as errorCode:
        pass
        return False   

def build_config(_config_file):
    config = configparser.ConfigParser()

    if not config.read(_config_file):
        sys.exit('Configuration file \''+_config_file+'\' is not a valid configuration file! Exiting...')        

    CONFIG = {}
    CONFIG['GLOBAL'] = {}
    CONFIG['REPORTS'] = {}
    CONFIG['LOGGER'] = {}
    CONFIG['ALIASES'] = {}
    CONFIG['SYSTEMS'] = {}
    CONFIG['ALLSTAR'] = {}

    try:
        for section in config.sections():
            if section == 'GLOBAL':
                CONFIG['GLOBAL'].update({
                    'PATH': config.get(section, 'PATH'),
                    'PING_TIME': config.getint(section, 'PING_TIME'),
                    'MAX_MISSED': config.getint(section, 'MAX_MISSED'),
                    'USE_ACL': config.get(section, 'USE_ACL'),
                    'REG_ACL': config.get(section, 'REG_ACL'),
                    'SUB_ACL': config.get(section, 'SUB_ACL'),
                    'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                    'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                    'GEN_STAT_BRIDGES': config.getboolean(section, 'GEN_STAT_BRIDGES'),
                    'ALLOW_NULL_PASSPHRASE': config.getboolean(section, 'ALLOW_NULL_PASSPHRASE'),
                    'ANNOUNCEMENT_LANGUAGES': config.get(section, 'ANNOUNCEMENT_LANGUAGES'),
                    'SERVER_ID': config.getint(section, 'SERVER_ID').to_bytes(4, 'big'),
                    'DATA_GATEWAY': config.getboolean(section, 'DATA_GATEWAY'),
                    'VALIDATE_SERVER_IDS': config.getboolean(section, 'VALIDATE_SERVER_IDS')
                    
                })
                if not CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES']:
                    CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'] = languages

            elif section == 'REPORTS':
                CONFIG['REPORTS'].update({
                    'REPORT': config.getboolean(section, 'REPORT'),
                    'REPORT_INTERVAL': config.getint(section, 'REPORT_INTERVAL'),
                    'REPORT_PORT': config.getint(section, 'REPORT_PORT'),
                    'REPORT_CLIENTS': config.get(section, 'REPORT_CLIENTS').split(',')
                })

            elif section == 'LOGGER':
                CONFIG['LOGGER'].update({
                    'LOG_FILE': config.get(section, 'LOG_FILE'),
                    'LOG_HANDLERS': config.get(section, 'LOG_HANDLERS'),
                    'LOG_LEVEL': config.get(section, 'LOG_LEVEL'),
                    'LOG_NAME': config.get(section, 'LOG_NAME')
                })
                if not CONFIG['LOGGER']['LOG_FILE']:
                    CONFIG['LOGGER']['LOG_FILE'] = '/dev/null'

            elif section == 'ALIASES':
                CONFIG['ALIASES'].update({
                    'TRY_DOWNLOAD': config.getboolean(section, 'TRY_DOWNLOAD'),
                    'PATH': config.get(section, 'PATH'),
                    'PEER_FILE': config.get(section, 'PEER_FILE'),
                    'SUBSCRIBER_FILE': config.get(section, 'SUBSCRIBER_FILE'),
                    'TGID_FILE': config.get(section, 'TGID_FILE'),
                    'PEER_URL': config.get(section, 'PEER_URL'),
                    'SUBSCRIBER_URL': config.get(section, 'SUBSCRIBER_URL'),
                    'TGID_URL': config.get(section, 'TGID_URL'),
                    'STALE_TIME': config.getint(section, 'STALE_DAYS') * 86400,
                    'SUB_MAP_FILE': config.get(section, 'SUB_MAP_FILE'),
                    'LOCAL_SUBSCRIBER_FILE': config.get(section, 'LOCAL_SUBSCRIBER_FILE'),
                    'SERVER_ID_URL': config.get(section, 'SERVER_ID_URL'),
                    'SERVER_ID_FILE': config.get(section, 'SERVER_ID_FILE')
                    
                })
                
                
            elif section == 'ALLSTAR':
                CONFIG['ALLSTAR'].update({
                    'ENABLED': config.getboolean(section, 'ENABLED'),
                    'USER': config.get(section, 'USER'),
                    'PASS': config.get(section, 'PASS'),
                    'SERVER': config.get(section, 'SERVER'),
                    'PORT': config.getint(section,'PORT'),
                    'NODE' : config.getint(section,'NODE')
            })
                
            elif section == 'PROXY':
                pass

            elif config.getboolean(section, 'ENABLED'):
                if config.get(section, 'MODE') == 'PEER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'LOOSE': config.getboolean(section, 'LOOSE'),
                        'SOCK_ADDR': (gethostbyname(config.get(section, 'IP')), config.getint(section, 'PORT')),
                        'IP': gethostbyname(config.get(section, 'IP')),
                        'PORT': config.getint(section, 'PORT'),
                        'MASTER_SOCKADDR': (gethostbyname(config.get(section, 'MASTER_IP')), config.getint(section, 'MASTER_PORT')),
                        'MASTER_IP': gethostbyname(config.get(section, 'MASTER_IP')),
                        '_MASTER_IP': config.get(section, 'MASTER_IP'),
                        'MASTER_PORT': config.getint(section, 'MASTER_PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        'CALLSIGN': bytes(config.get(section, 'CALLSIGN').ljust(8)[:8], 'utf-8'),
                        'RADIO_ID': config.getint(section, 'RADIO_ID').to_bytes(4, 'big'),
                        'RX_FREQ': bytes(config.get(section, 'RX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_FREQ': bytes(config.get(section, 'TX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_POWER': bytes(config.get(section, 'TX_POWER').rjust(2,'0'), 'utf-8'),
                        'COLORCODE': bytes(config.get(section, 'COLORCODE').rjust(2,'0'), 'utf-8'),
                        'LATITUDE': bytes(config.get(section, 'LATITUDE').ljust(8)[:8], 'utf-8'),
                        'LONGITUDE': bytes(config.get(section, 'LONGITUDE').ljust(9)[:9], 'utf-8'),
                        'HEIGHT': bytes(config.get(section, 'HEIGHT').rjust(3,'0'), 'utf-8'),
                        'LOCATION': bytes(config.get(section, 'LOCATION').ljust(20)[:20], 'utf-8'),
                        'DESCRIPTION': bytes(config.get(section, 'DESCRIPTION').ljust(19)[:19], 'utf-8'),
                        'SLOTS': bytes(config.get(section, 'SLOTS'), 'utf-8'),
                        'URL': bytes(config.get(section, 'URL').ljust(124)[:124], 'utf-8'),
                        'SOFTWARE_ID': bytes(config.get(section, 'SOFTWARE_ID').ljust(40)[:40], 'utf-8'),
                        'PACKAGE_ID': bytes(config.get(section, 'PACKAGE_ID').ljust(40)[:40], 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        'OPTIONS': bytes(config.get(section, 'OPTIONS'), 'utf-8'),
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE')
                    }})
                    CONFIG['SYSTEMS'][section].update({'STATS': {
                        'CONNECTION': 'NO',             # NO, RTPL_SENT, AUTHENTICATED, CONFIG-SENT, YES 
                        'CONNECTED': None,
                        'PINGS_SENT': 0,
                        'PINGS_ACKD': 0,
                        'NUM_OUTSTANDING': 0,
                        'PING_OUTSTANDING': False,
                        'LAST_PING_TX_TIME': 0,
                        'LAST_PING_ACK_TIME': 0,
                    }})

                if config.get(section, 'MODE') == 'XLXPEER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'LOOSE': config.getboolean(section, 'LOOSE'),
                        'SOCK_ADDR': (gethostbyname(config.get(section, 'IP')), config.getint(section, 'PORT')),
                        'IP': gethostbyname(config.get(section, 'IP')),
                        'PORT': config.getint(section, 'PORT'),
                        'MASTER_SOCKADDR': (gethostbyname(config.get(section, 'MASTER_IP')), config.getint(section, 'MASTER_PORT')),
                        'MASTER_IP': gethostbyname(config.get(section, 'MASTER_IP')),
                        '_MASTER_IP': config.get(section, 'MASTER_IP'),
                        'MASTER_PORT': config.getint(section, 'MASTER_PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        'CALLSIGN': bytes(config.get(section, 'CALLSIGN').ljust(8)[:8], 'utf-8'),
                        'RADIO_ID': config.getint(section, 'RADIO_ID').to_bytes(4, 'big'),
                        'RX_FREQ': bytes(config.get(section, 'RX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_FREQ': bytes(config.get(section, 'TX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_POWER': bytes(config.get(section, 'TX_POWER').rjust(2,'0'), 'utf-8'),
                        'COLORCODE': bytes(config.get(section, 'COLORCODE').rjust(2,'0'), 'utf-8'),
                        'LATITUDE': bytes(config.get(section, 'LATITUDE').ljust(8)[:8], 'utf-8'),
                        'LONGITUDE': bytes(config.get(section, 'LONGITUDE').ljust(9)[:9], 'utf-8'),
                        'HEIGHT': bytes(config.get(section, 'HEIGHT').rjust(3,'0'), 'utf-8'),
                        'LOCATION': bytes(config.get(section, 'LOCATION').ljust(20)[:20], 'utf-8'),
                        'DESCRIPTION': bytes(config.get(section, 'DESCRIPTION').ljust(19)[:19], 'utf-8'),
                        'SLOTS': bytes(config.get(section, 'SLOTS'), 'utf-8'),
                        'URL': bytes(config.get(section, 'URL').ljust(124)[:124], 'utf-8'),
                        'SOFTWARE_ID': bytes(config.get(section, 'SOFTWARE_ID').ljust(40)[:40], 'utf-8'),
                        'PACKAGE_ID': bytes(config.get(section, 'PACKAGE_ID').ljust(40)[:40], 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        'XLXMODULE': config.getint(section, 'XLXMODULE'),
                        'OPTIONS': '',
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE')
                    }})
                    CONFIG['SYSTEMS'][section].update({'XLXSTATS': {
                        'CONNECTION': 'NO',             # NO, RTPL_SENT, AUTHENTICATED, CONFIG-SENT, YES 
                        'CONNECTED': None,
                        'PINGS_SENT': 0,
                        'PINGS_ACKD': 0,
                        'NUM_OUTSTANDING': 0,
                        'PING_OUTSTANDING': False,
                        'LAST_PING_TX_TIME': 0,
                        'LAST_PING_ACK_TIME': 0,
                    }})

                elif config.get(section, 'MODE') == 'MASTER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'REPEAT': config.getboolean(section, 'REPEAT'),
                        'MAX_PEERS': config.getint(section, 'MAX_PEERS'),
                        'IP': config.get(section, 'IP'),
                        'PORT': config.getint(section, 'PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'REG_ACL': config.get(section, 'REG_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                        'DEFAULT_UA_TIMER': config.getint(section, 'DEFAULT_UA_TIMER'),
                        'SINGLE_MODE': config.getboolean(section, 'SINGLE_MODE'),
                        'VOICE_IDENT': config.getboolean(section, 'VOICE_IDENT'),
                        'TS1_STATIC': config.get(section,'TS1_STATIC'),
                        'TS2_STATIC': config.get(section,'TS2_STATIC'),
                        'DEFAULT_REFLECTOR': config.getint(section, 'DEFAULT_REFLECTOR'),
                        'GENERATOR': config.getint(section, 'GENERATOR'),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE'),
                        'ALLOW_UNREG_ID': config.getboolean(section,'ALLOW_UNREG_ID'),
                        'PROXY_CONTROL' : config.getboolean(section,'PROXY_CONTROL'),
                        'OVERRIDE_IDENT_TG': config.get(section, 'OVERRIDE_IDENT_TG')
                    }})
                    CONFIG['SYSTEMS'][section].update({'PEERS': {}})
                    
                elif config.get(section, 'MODE') == 'OPENBRIDGE':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'NETWORK_ID': config.getint(section, 'NETWORK_ID').to_bytes(4, 'big'),
                        #'OVERRIDE_SERVER_ID': config.getint(section, 'OVERRIDE_SERVER_ID').to_bytes(4, 'big'),
                        'IP': config.get(section, 'IP'),
                        'PORT': config.getint(section, 'PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE').ljust(20,'\x00')[:20], 'utf-8'),
                        #'TARGET_SOCK': (gethostbyname(config.get(section, 'TARGET_IP')), config.getint(section, 'TARGET_PORT')),
                        'TARGET_IP': config.get(section, 'TARGET_IP'),
                        'TARGET_PORT': config.getint(section, 'TARGET_PORT'),
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_ACL'),
                        'TG2_ACL': 'PERMIT:ALL',
                        'RELAX_CHECKS': config.getboolean(section, 'RELAX_CHECKS'),
                        'ENHANCED_OBP': config.getboolean(section, 'ENHANCED_OBP'),
                        'VER' : config.getint(section, 'PROTO_VER')
                    }})
                    
                    try:
                        
                        if CONFIG['SYSTEMS'][section]['IP'] == '::':
                            try:
                                addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET6, socket.IPPROTO_IP)
                            except gaierror:
                                 addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET, socket.IPPROTO_IP)
                        
                        elif CONFIG['SYSTEMS'][section]['IP'] and IsIPv6Address(CONFIG['SYSTEMS'][section]['IP']):
                            addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET6, socket.IPPROTO_IP)
                                
                        elif not CONFIG['SYSTEMS'][section]['IP'] or IsIPv4Address(CONFIG['SYSTEMS'][section]['IP']):
                            addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET, socket.IPPROTO_IP)
                        else:
                            raise
                        
                        family, socktype, proto, canonname, sockaddr = addr_info[0]
                        CONFIG['SYSTEMS'][section]['TARGET_IP'] = sockaddr[0]
                        
                        if CONFIG['SYSTEMS'][section]['IP'] == '::' and IsIPv4Address(CONFIG['SYSTEMS'][section]['TARGET_IP']):
                                CONFIG['SYSTEMS'][section]['TARGET_IP'] = '::ffff:' + CONFIG['SYSTEMS'][section]['TARGET_IP']
                        
                        CONFIG['SYSTEMS'][section]['TARGET_SOCK'] = (CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'])
                    
                    except:
                        CONFIG['SYSTEMS'][section]['TARGET_IP'] = False
                        CONFIG['SYSTEMS'][section]['TARGET_SOCK'] = (CONFIG['SYSTEMS'][section]['TARGET_IP'], CONFIG['SYSTEMS'][section]['TARGET_PORT'])
                        
    
    except configparser.Error as err:
        sys.exit('Error processing configuration file -- {}'.format(err))
        
    process_acls(CONFIG)
    
    return CONFIG

# Used to run this file direclty and print the config,
# which might be useful for debugging
if __name__ == '__main__':
    import sys
    import os
    import argparse
    from pprint import pprint
    from dmr_utils3.utils import int_id
    
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    cli_args = parser.parse_args()


    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'
    
    CONFIG = build_config(cli_args.CONFIG_FILE)
    pprint(CONFIG)
    
    def acl_check(_id, _acl):
        id = int_id(_id)
        for entry in _acl[1]:
            if entry[0] <= id <= entry[1]:
                return _acl[0]
        return not _acl[0]
        
    print(acl_check(b'\x00\x01\x37', CONFIG['GLOBAL']['TG1_ACL']))
