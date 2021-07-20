#!/bin/bash

echo FreeDMR Docker installer...

echo Installing required packages...
apt-get -y install docker.io && 
apt-get -y install docker-compose &&
apt-get -y  install conntrack &&

echo Set userland-proxy to false and enable ipv6...
cat  > /etc/docker/daemon.json &&
{
     "userland-proxy": false,
     "fixed-cidr-v6": "fd2a:70b6:9f54:29b5::/64",
     "ipv6": true
}
EOF

echo Restart docker...
systemctl restart docker &&

echo Make config directory...
mkdir /etc/freedmr &&
chmod 755 /etc/freedmr &&

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
ANNOUNCEMENT_LANGUAGES: en_GB,en_GB_2,en_US,es_ES,es_ES_2,fr_FR,de_DE,dk_DK,it_IT,no_NO,pl_PL,se_SE,CW
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
SUBSCRIBER_URL: https://www.radioid.net/static/users.json
TGID_URL: TGID_URL: http://downloads.freedmr.uk/downloads/talkgroup_ids.json
STALE_DAYS: 7

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
OVERRIDE_SERVER_ID: False
NETWORK_ID: 1
PASSPHRASE: mypass
TARGET_IP: 
TARGET_PORT: 62044
USE_ACL: True
SUB_ACL: DENY:1
TGID_ACL: PERMIT:ALL
RELAX_CHECKS: True
ENHANCED_OBP: True


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
ANNOUNCEMENT_LANGUAGE: en_GB_2
EOF

echo Install rules.py ...
echo "BRIDGES = {'9990': [{'SYSTEM': 'ECHO', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/freedmr/rules.py &&

echo Set perms on config directory...
chown -R 54000 /etc/freedmr &&

echo install JSON files...
mkdir -p /etc/freedmr/json &&
curl https://www.radioid.net/static/rptrs.json -o /etc/freedmr/json/peer_ids.json && 
curl https://www.radioid.net/static/users.json -o /etc/freedmr/json/subscriber_ids.json &&
curl http://downloads.freedmr.uk/downloads/talkgroup_ids.json -o /etc/freedmr/json/talkgroup_ids.json &&
chmod 777 -R /etc/freedmr/json &&

echo Setup logging...
mkdir -p /var/log/freedmr &&
touch /var/log/freedmr/freedmr.log &&
chown -R 54000 /var/log/freedmr &&
mkdir -p /var/log/FreeDMRmonitor &&
touch /var/log/FreeDMRmonitor/hbmon.log &&
touch /var/log/FreeDMRmonitor/lastheard.log &&

echo Setup lastheard pruning cron job...
cat << EOF > /etc/cron.hourly/lastheard
#!/bin/bash
mv /var/log/FreeDMRmonitor/lastheard.log /var/log/FreeDMRmonitor/lastheard.log.save
/usr/bin/tail -150 /var/log/FreeDMRmonitor/lastheard.log.save > /var/log/FreeDMRmonitor/lastheard.log
mv /var/log/FreeDMRmonitor/lastheard.log /var/log/FreeDMRmonitor/lastheard.log.save
/usr/bin/tail -150 /var/log/FreeDMRmonitor/lastheard.log.save > /var/log/FreeDMRmonitor/lastheard.log
EOF

echo Get docker-compose.yml...
cd /etc/freedmr &&
curl https://raw.githubusercontent.com/hacknix/FreeDMR/master/docker-configs/docker-compose-ipv6.yml -o docker-compose.yml

echo Run FreeDMR containers...
docker-compose up -d


echo FreeDMR setup complete!
echo try:
echo 'tail -f /var/log/freedmr/freedmr.log'
