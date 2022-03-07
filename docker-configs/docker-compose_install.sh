#!/bin/bash
#
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
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

echo FreeDMR Docker installer...

echo Installing required packages...
apt-get -y install docker.io && 
apt-get -y install docker-compose &&
apt-get -y  install conntrack &&

echo Set userland-proxy to false...
echo '{ "userland-proxy": false}' > /etc/docker/daemon.json &&

echo Restart docker...
systemctl restart docker &&

echo Make config directory...
mkdir /etc/freedmr &&
chmod 755 /etc/freedmr &&

echo make json directory...
mkdir -p /etc/freedmr/json &&

echo get json files...
cd /etc/freedmr/json &&
curl http://downloads.freedmr.uk/downloads/local_subscriber_ids.json -o subscriber_ids.json &&
curl http://downloads.freedmr.uk/downloads/talkgroup_ids.json -o talkgroup_ids.json &&
curl https://www.radioid.net/static/rptrs.json -o peer_ids.json &&
touch /etc/freedmr/json/sub_map.pkl &&
chmod -R 777 /etc/freedmr/json &&

echo Install /etc/freedmr/freedmr.cfg ... 
cat << EOF > /etc/freedmr/freedmr.cfg
[GLOBAL]
PATH: ./
PING_TIME: 10
MAX_MISSED: 3
USE_ACL: True
REG_ACL: DENY:0-100000
SUB_ACL: DENY:0-100000
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
GEN_STAT_BRIDGES: True
ALLOW_NULL_PASSPHRASE: True
ANNOUNCEMENT_LANGUAGES:
SERVER_ID: 0


[REPORTS]
REPORT: True
REPORT_INTERVAL: 60
REPORT_PORT: 4321
REPORT_CLIENTS: *

[LOGGER]
LOG_FILE: freedmr.log
LOG_HANDLERS: file-timed
LOG_LEVEL: INFO
LOG_NAME: FreeDMR

[ALIASES]
TRY_DOWNLOAD: False
PATH: ./
PEER_FILE: peer_ids.json
SUBSCRIBER_FILE: subscriber_ids.json
TGID_FILE: talkgroup_ids.json
PEER_URL: https://www.radioid.net/static/rptrs.json
SUBSCRIBER_URL: http://downloads.freedmr.uk/downloads/local_subscriber_ids.json
TGID_URL: TGID_URL: http://downloads.freedmr.uk/downloads/talkgroup_ids.json
STALE_DAYS: 7
LOCAL_SUBSCRIBER_FILE: local_subcriber_ids.json
SUB_MAP_FILE: sub_map.pkl

[MYSQL]
USE_MYSQL: False
USER: hblink
PASS: mypassword
DB: hblink
SERVER: 127.0.0.1
PORT: 3306
TABLE: repeaters

[OBP-TEST]
MODE: OPENBRIDGE
ENABLED: False
IP:
PORT: 62044
NETWORK_ID: 1
PASSPHRASE: mypass
TARGET_IP: 
TARGET_PORT: 62044
USE_ACL: True
SUB_ACL: DENY:1
TGID_ACL: PERMIT:ALL
RELAX_CHECKS: True
ENHANCED_OBP: True
PROTO_VER: 2


[SYSTEM]
MODE: MASTER
ENABLED: True
REPEAT: True
MAX_PEERS: 1
EXPORT_AMBE: False
IP: 127.0.0.1
PORT: 54000
PASSPHRASE:
GROUP_HANGTIME: 5
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
DEFAULT_UA_TIMER: 10
SINGLE_MODE: True
VOICE_IDENT: True
TS1_STATIC:
TS2_STATIC:
DEFAULT_REFLECTOR: 0
ANNOUNCEMENT_LANGUAGE: en_GB
GENERATOR: 100
ALLOW_UNREG_ID: False
PROXY_CONTROL: True

[ECHO]
MODE: PEER
ENABLED: True
LOOSE: False
EXPORT_AMBE: False
IP: 127.0.0.1
PORT: 54916
MASTER_IP: 127.0.0.1
MASTER_PORT: 54915
PASSPHRASE: passw0rd
CALLSIGN: ECHO
RADIO_ID: 1000001
RX_FREQ: 449000000
TX_FREQ: 444000000
TX_POWER: 25
COLORCODE: 1
SLOTS: 1
LATITUDE: 00.0000
LONGITUDE: 000.0000
HEIGHT: 0
LOCATION: Earth
DESCRIPTION: ECHO
URL: www.freedmr.uk
SOFTWARE_ID: 20170620
PACKAGE_ID: MMDVM_FreeDMR
GROUP_HANGTIME: 5
OPTIONS:
USE_ACL: True
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
ANNOUNCEMENT_LANGUAGE: en_GB
EOF

echo Install rules.py ...
echo "BRIDGES = {'9990': [{'SYSTEM': 'ECHO', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/freedmr/rules.py &&

echo Set perms on config directory...
chown -R 54000 /etc/freedmr &&

echo Setup logging...
mkdir -p /var/log/freedmr &&
touch /var/log/freedmr/freedmr.log &&
chown -R 54000 /var/log/freedmr &&
mkdir -p /var/log/FreeDMRmonitor &&
touch /var/log/FreeDMRmonitor/lastheard.log &&
touch /var/log/FreeDMRmonitor/hbmon.log &&
chown -R 54001 /var/log/FreeDMRmonitor &&

echo Get docker-compose.yml...
cd /etc/freedmr &&
curl https://gitlab.hacknix.net/hacknix/FreeDMR/-/raw/master/docker-configs/docker-compose.yml -o docker-compose.yml &&
echo Install crontab...
cat << EOF > /etc/cron.daily/lastheard
#!/bin/bash
mv /var/log/FreeDMRmonitor/lastheard.log /var/log/FreeDMRmonitor/lastheard.log.save
/usr/bin/tail -150 /var/log/FreeDMRmonitor/lastheard.log.save > /var/log/FreeDMRmonitor/lastheard.log
mv /var/log/FreeDMRmonitor/lastheard.log /var/log/FreeDMRmonitor/lastheard.log.save
/usr/bin/tail -150 /var/log/FreeDMRmonitor/lastheard.log.save > /var/log/FreeDMRmonitor/lastheard.log
EOF
chmod 755 /etc/cron.daily/lastheard


echo Run FreeDMR container...
docker-compose up -d


echo FreeDMR setup complete!
