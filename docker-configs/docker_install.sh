#!/bin/bash

echo RYSEN Docker installer...

echo Installing required packages...
apt-get -y install docker.io && 
#apt-get -y install docker-compose &&
apt-get -y  install conntrack &&

echo Set userland-proxy to false...
echo '{ "userland-proxy": false}' > /etc/docker/daemon.json &&

echo Restart docker...
systemctl restart docker &&

echo Pull RYSEN latest image...
docker pull hacknix/rysen:latest &&

echo Make config directory...
mkdir /etc/rysen &&
chmod 755 /etc/rysen &&

echo Install /etc/rysen/rysen.cfg ... 
cat << EOF > /etc/rysen/rysen.cfg
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
DATA_GATEWAY: False

[REPORTS]
REPORT: True
REPORT_INTERVAL: 60
REPORT_PORT: 4321
REPORT_CLIENTS: *

[LOGGER]
LOG_FILE: rysen.log
LOG_HANDLERS: file-timed
LOG_LEVEL: INFO
LOG_NAME: RYSEN

[ALIASES]
TRY_DOWNLOAD: False
PATH: ./
PEER_FILE: peer_ids.json
SUBSCRIBER_FILE: subscriber_ids.json
TGID_FILE: talkgroup_ids.json
PEER_URL: https://www.radioid.net/static/rptrs.json
SUBSCRIBER_URL: https://www.radioid.net/static/users.json
TGID_URL: TGID_URL: http://downloads.freedmr.uk/downloads/talkgroup_ids.json
STALE_DAYS: 1
SERVER_ID_URL: http://downloads.freedmr.uk/downloads/FreeDMR_Hosts.csv
SERVER_ID_FILE: server_ids.tsv


#Control server shared allstar instance via dial / AMI
[ALLSTAR]
ENABLED: False
USER:llcgi
PASS: mypass
SERVER: my.asl.server
PORT: 5038
NODE: 0000


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
PROTO_VER: 4

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
ANNOUNCEMENT_LANGUAGE: en_GB_2
GENERATOR: 100

[PARROT]
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
LOCATION: 9990
DESCRIPTION: PARROT
URL: www.freestar.network
SOFTWARE_ID: 20170620
PACKAGE_ID: MMDVM_SYSTEM-X
GROUP_HANGTIME: 5
OPTIONS:
USE_ACL: True
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
ANNOUNCEMENT_LANGUAGE: en_GB_2
EOF

echo Install rules.py ...
echo "BRIDGES = {'9990': [{'SYSTEM': 'PARROT', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/rysen/rules.py &&

echo Set perms on config directory...
chown -R 54000 /etc/rysen &&

echo Setup logging...
mkdir -p /var/log/rysen &&
touch /var/log/rysen/rysen.log &&
chown -R 54000 /var/log/rysen &&

echo Run RYSEN container...
docker run --name=rysen -d --read-only -v /etc/rysen/rysen.cfg:/opt/rysen/rysen.cfg \
-v /var/log/rysen/rysen.log:/opt/rysen/rysen.log \
-v /etc/rysen/rules.py:/opt/rysen/rules.py -p 62031:62031/udp -p 62036-62046:62036-62046/udp \
-p 4321:4321/tcp shaymez/rysen:latest &&

echo Set to restart on boot and when it dies...
docker update --restart unless-stopped rysen &&

echo Download update script for future use...
curl https://raw.githubusercontent.com/ShaYmez/RYSEN/master/docker-configs/update_rysen.sh -o update_rysen.sh &&
chmod 700 ./update_rysen.sh

echo RYSEN setup complete!


