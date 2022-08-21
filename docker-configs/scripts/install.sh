#!/bin/bash
# RYSEN DMRMaster+ ver 1.3.4 Installer ver 1.2.1. systemx-docker-installer
#
##################################################################################
#   Copyright (C) 2021-2022 Shane Daley, M0VUB aka ShaYmez. <support@gb7nr.co.uk>
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
##################################################################################
#
# A tool to install RYSEN DMRMaster+ Docker with Debian / Ubuntu support.
# This essentially is a fully fletched master server installed with dashboard / SSL ready to go.
# Step 1: Install Debian 10 or 11 or Ubuntu 20.04 onwards.. and make sure it has internet and is up to date.
# Step 2: Run this script on the computer.
# Step 4: Reboot after installation.
# This is a docker version and you can use the following commands to control / maintain your server
# cd /etc/hblink3
# docker-compose up -d (starts the hblink3 docker container)
# docker-compose down (shuts down the hblink container and stops the service)
# docker-compose pull (updates the container to the latest docker image)
# systemctl |stop|start|restart|status hbmon (controls the HBMonv2 dash service)
# logs can be found in var/log/hblink or docker comand "docker container logs hblink"
#Lets begin-------------------------------------------------------------------------------------------------
if [ "$EUID" -ne 0 ];
then
  echo ""
  echo "You Must be root to run this script!!"
  exit 1
fi
if [ ! -e "/etc/debian_version" ]
then
  echo ""
  echo "This script is only tested in Debian 9,10 & 11 repo only."
  exit 0
fi
DIRDIR=$(pwd)
LOCAL_IP=$(ip a | grep inet | grep "eth0\|en" | awk '{print $2}' | tr '/' ' ' | awk '{print $1}')
EXTERNAL_IP=$(curl https://ipecho.net/plain)
ARC=$(lscpu | grep Arch | awk '{print $2}')
VERSION=$(sed 's/\..*//' /etc/debian_version)
ARMv7l=https://get.docker.com | sh
ARMv8l=https://get.docker.com | sh
X32=https://get.docker.com | sh
X64=https://get.docker.com | sh
INSDIR=/opt/tmp/
FDINSDIR=/opt/RYSEN/
HBMONDIR=/opt/HBMonv2/
FDDIR=/etc/rysen/
DEP="wget curl git python3 python3-dev python3-pip libffi-dev libssl-dev conntrack sed cargo apache2 php snapd figlet ca-certificates gnupg lsb-release"
DEP1="wget curl git python3 python3-dev python3-pip libffi-dev libssl-dev conntrack sed cargo apache2 php snapd figlet ca-certificates gnupg lsb-release"
RYGITMONREPO=https://github.com/ShaYmez/RYSEN-dash.git
RYGITMONHTML=https://github.com/ShaYmez/RYSEN-dash-html.git
FDGITXREPO=https://github.com/ShaYmez/RYSEN.git
echo ""
echo "------------------------------------------------------------------------------"
echo "Downloading and installing required software & dependencies....."
echo "------------------------------------------------------------------------------"

        if [ $VERSION = 10 ];
        then
                apt-get update
                apt-get install -y $DEP
                sleep 2
                apt-get remove docker docker-engine docker.io containerd runc
                curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
                
                echo \
                "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian \
                $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
                
                apt-get update
                apt-get install -y docker-ce docker-ce-cli containerd.io
                apt-get install -y docker-compose
                systemctl enable docker
                systemctl start docker
                figlet "docker.io"
                echo Set userland-proxy to false...
                echo '{ "userland-proxy": false}' > /etc/docker/daemon.json
        elif [ $VERSION = 11 ];
        then
                apt-get update
                apt-get install -y $DEP
                sleep 2
                apt-get remove docker docker-engine docker.io containerd runc
                curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
                
                echo \
                "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian \
                $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
                
                apt-get update
                apt-get install -y docker-ce docker-ce-cli containerd.io
                apt-get install -y docker-compose
                systemctl enable docker
                systemctl start docker
                figlet "docker.io"
                echo Set userland-proxy to false...
                echo '{ "userland-proxy": false}' > /etc/docker/daemon.json
        else
        echo "-------------------------------------------------------------------------------------------"
        echo "Operating system not supported! Please check your configuration or upgrade. Exiting....."
        echo "-------------------------------------------------------------------------------------------"
        exit 0
fi
echo "Done."
echo "------------------------------------------------------------------------------"
echo "Downloading and installing RYSEN Dashboard....."
echo "------------------------------------------------------------------------------"
sleep 2
cd /opt/
mkdir -p tmp
chmod 0755 /opt/tmp/
cd /opt/
git clone $RYGITMONREPO
cd $HBMONDIR
if [ -e monitor.py ]
then
        echo "--------------------------------------------------------------------------------"
        echo "It looks like RYSEN Dashboard installed correctly. The installation will now proceed. "
        echo "--------------------------------------------------------------------------------"
        else
        echo "-------------------------------------------------------------------------------------------"
        echo "I dont see RYSEN Dashboard installed! Please check your configuration and try again. Exiting....."
        echo "-------------------------------------------------------------------------------------------"
        exit 0
fi
echo "Done."
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Installing RYSEN Dashboard configuration....."
echo "------------------------------------------------------------------------------"
sleep 2
                pip3 install setuptools wheel
                pip3 install -r requirements.txt
        echo Install /opt/RYSEN-dash/config.py ...
cat << EOF > /opt/RYSEN-dash/config.py
CONFIG_INC      = True                           # Include HBlink stats
HOMEBREW_INC    = True                           # Display Homebrew Peers status
LASTHEARD_INC   = True                           # Display lastheard table on main page
BRIDGES_INC     = False                          # Display Bridge status and button
EMPTY_MASTERS   = False                          # Display Enable (True) or DISABLE (False) empty masters in status
#
HBLINK_IP       = '127.0.0.1'                    # HBlink's IP Address
HBLINK_PORT     = 4321                           # HBlink's TCP reporting socket
FREQUENCY       = 10                             # Frequency to push updates to web clients
CLIENT_TIMEOUT  = 0                              # Clients are timed out after this many seconds, 0 to disable

# Generally you don't need to use this but
# if you don't want to show in lastherad received traffic from OBP link put NETWORK ID 
# for example: "260210,260211,260212"
OPB_FILTER = ""

# Files and stuff for loading alias files for mapping numbers to names
PATH            = './'                           # MUST END IN '/'
PEER_FILE       = 'peer_ids.json'                # Will auto-download 
SUBSCRIBER_FILE = 'subscriber_ids.json'          # Will auto-download 
TGID_FILE       = 'talkgroup_ids.json'           # User provided
LOCAL_SUB_FILE  = 'local_subscriber_ids.json'    # User provided (optional, leave '' if you don't use it)
LOCAL_PEER_FILE = 'local_peer_ids.json'          # User provided (optional, leave '' if you don't use it)
LOCAL_TGID_FILE = 'local_talkgroup_ids.json'     # User provided (optional, leave '' if you don't use it)
FILE_RELOAD     = 14                             # Number of days before we reload DMR-MARC database files
PEER_URL        = 'https://database.radioid.net/static/rptrs.json'
SUBSCRIBER_URL  = 'https://database.radioid.net/static/users.json'

# Settings for log files
LOG_PATH        = './log/'             # MUST END IN '/'
LOG_NAME        = 'hbmon.log'
EOF
                cp utils/hbmon.service /lib/systemd/system/
                cp utils/lastheard /etc/cron.daily/
                chmod +x /etc/cron.daily/lastheard
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Installing RYSEN HTML frontend....."
echo "------------------------------------------------------------------------------"
sleep 2
                cd $INSDIR
                git clone $RYGITMONHTTML
                mv /var/www/html/index.html /var/www/html/index_APACHE.html
                cp -a /opt/tmp/RYSEN-dash-html/users/html/. /var/www/html/
                
if [ -e /var/www/html/info.php ]
then
        echo "------------------------------------------------------------------------------------"
        echo "It looks like the dashboard installed correctly. The installation will now proceed. "
        echo "------------------------------------------------------------------------------------"
        else
        echo "-----------------------------------------------------------------------------------------------"
        echo "I dont see the dashboard installed! Please check your configuration and try again. Exiting....."
        echo "-----------------------------------------------------------------------------------------------"
        exit 0
fi
echo "Done."

echo "Install crontab..."
cat << EOF > /etc/cron.daily/lastheard
#!/bin/bash
mv /opt/RYSEN-dash/log/lastheard.log /opt/RYSEN-dash/log/lastheard.log.save
/usr/bin/tail -150 /opt/RYSEN-dash/log/lastheard.log.save > /opt/RYSEN-dash/log/lastheard.log
mv /opt/RYSEN-dash/log/lastheard.log /opt/RYSEN-dash/log/lastheard.log.save
/usr/bin/tail -150 /opt/RYSEN-dash/log/lastheard.log.save > /opt/RYSEN-dash/log/lastheard.log
EOF
chmod 755 /etc/cron.daily/lastheard

sleep 2
echo "------------------------------------------------------------------------------"
echo "Installing RYSEN configuration directories....."
echo "------------------------------------------------------------------------------"
sleep 2
         echo Restart docker...
         systemctl restart docker
         sleep 3

         echo Make config directory...
         mkdir -p /etc/rysen

         echo make json directory...
         mkdir -p /etc/rysen/json/
         
echo "Done"
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Installing RYSEN configuration files....."
echo "------------------------------------------------------------------------------"
sleep 2
        echo Install /etc/rysen/rysen.cfg ... 
cat << EOF > /etc/rysen/rysen.cfg
# RYSEN DMRMaster+ Version 1.3.8 
# PATH - working path for files, leave it alone unless you NEED to change it
# PING_TIME - the interval that peers will ping the master, and re-try registraion
#           - how often the Master maintenance loop runs
# MAX_MISSED - how many pings are missed before we give up and re-register
#           - number of times the master maintenance loop runs before de-registering a peer
#
# ACLs:
#
# Access Control Lists are a very powerful tool for administering your system.
# But they consume packet processing time. Disable them if you are not using them.
# But be aware that, as of now, the configuration stanzas still need the ACL
# sections configured even if you're not using them.
#
# REGISTRATION ACLS ARE ALWAYS USED, ONLY SUBSCRIBER AND TGID MAY BE DISABLED!!!
#
# The 'action' May be PERMIT|DENY
# Each entry may be a single radio id, or a hypenated range (e.g. 1-2999)
# Format:
# 	ACL = 'action:id|start-end|,id|start-end,....'
#		--for example--
#	SUB_ACL: DENY:1,1000-2000,4500-60000,17
#
# ACL Types:
# 	REG_ACL: peer radio IDs for registration (only used on HBP master systems)
# 	SUB_ACL: subscriber IDs for end-users
# 	TGID_TS1_ACL: destination talkgroup IDs on Timeslot 1
# 	TGID_TS2_ACL: destination talkgroup IDs on Timeslot 2
#
# ACLs may be repeated for individual systems if needed for granularity
# Global ACLs will be processed BEFORE the system level ACLs
# Packets will be matched against all ACLs, GLOBAL first. If a packet 'passes'
# All elements, processing continues. Packets are discarded at the first
# negative match, or 'reject' from an ACL element.
#
# If you do not wish to use ACLs, set them to 'PERMIT:ALL'
# TGID_TS1_ACL in the global stanza is used for OPENBRIDGE systems, since all
# traffic is passed as TS 1 between OpenBridges
[GLOBAL]
PATH: ./
PING_TIME: 10
MAX_MISSED: 3
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: DENY:0-79
TGID_TS2_ACL: DENY:0-8,10-79
GEN_STAT_BRIDGES: True
ALLOW_NULL_PASSPHRASE: True
ANNOUNCEMENT_LANGUAGES: en_GB,en_GB_2,en_US,es_ES,fr_FR,de_DE,dk_DK,it_IT,no_NO,pl_PL,se_SE,pt_PT,cy_GB,el_GR,CW
VALIDATE_SERVER_IDS: False
SERVER_ID: 0
DATA_GATEWAY: False

# NETWORK REPORTING CONFIGURATION DASHBOARD SOCKET
#   Enabling "REPORT" will configure a socket-based reporting
#   system that will send the configuration and other items
#   to a another process (local or remote) that may process
#   the information for some useful purpose, like a web dashboard.
#
#   REPORT - True to enable, False to disable
#   REPORT_INTERVAL - Seconds between reports
#   REPORT_PORT - TCP port to listen on if "REPORT_NETWORKS" = NETWORK
#   REPORT_CLIENTS - comma separated list of IPs you will allow clients
#       to connect on. Entering a * will allow all.
#
# ****FOR NOW MUST BE TRUE - USE THE LOOPBACK IF YOU DON'T USE THIS!!!****
[REPORTS]
REPORT: True
REPORT_INTERVAL: 60
REPORT_PORT: 4321
REPORT_CLIENTS: *

# SYSTEM LOGGER CONFIGURAITON
#   This allows the logger to be configured without chaning the individual
#   python logger stuff. LOG_FILE should be a complete path/filename for *your*
#   system -- use /dev/null for non-file handlers.
#   LOG_HANDLERS may be any of the following, please, no spaces in the
#   list if you use several:
#       null
#       console
#       console-timed
#       file
#       file-timed
#       syslog
#   LOG_LEVEL 
# TRACE - Low-Level logging and packet data
# DEBUG - self explanatory 
# INFO - normal log level 
# WARNING - only abnormal states
# ERROR - only errors
# CRITICAL - only serious events

[LOGGER]
LOG_FILE: rysen.log
LOG_HANDLERS: file-timed
LOG_LEVEL: INFO
LOG_NAME: RYSEN

# DOWNLOAD AND IMPORT SUBSCRIBER, PEER and TGID ALIASES
# Ok, not the TGID, there's no master list I know of to download
# This is intended as a facility for other applcations built on top of
# HBlink to use, and will NOT be used in HBlink directly.
# STALE_DAYS is the number of days since the last download before we
# download again. Don't be an ass and change this to less than a few days.
[ALIASES]
TRY_DOWNLOAD: True
PATH: ./json/
PEER_FILE: peer_ids.json
SUBSCRIBER_FILE: subscriber_ids.json
TGID_FILE: talkgroup_ids.json
PEER_URL: https://www.radioid.net/static/rptrs.json
SUBSCRIBER_URL: https://www.radioid.net/static/users.json
LOCAL_SUBSCRIBER_URL: https://freestar.network/downloads/local_subscriber_ids.json
TGID_URL: https://freestar.network/downloads/talkgroup_ids.json
LOCAL_SUBSCRIBER_FILE: local_subcriber_ids.json
SERVER_ID_URL: https://freestar.network/downloads/SystemX_Hosts.csv
SERVER_ID_FILE: server_ids.tsv
STALE_DAYS: 14
SUB_MAP_FILE: sub_map.pkl

#Control server shared allstar instance via dial / AMI
[ALLSTAR]
ENABLED: False
USER:llcgi
PASS: mypass
SERVER: my.asl.server
PORT: 5038
NODE: 0000

#Read further repeater configs from MySQL
[MYSQL]
USE_MYSQL: False
USER: hblink
PASS: mypassword
DB: hblink
SERVER: 127.0.0.1
PORT: 3306
TABLE: repeaters

# OPENBRIDGE INSTANCES - DUPLICATE SECTION FOR MULTIPLE CONNECTIONS
# OpenBridge is a protocol originall created by DMR+ for connection between an
# IPSC2 server and Brandmeister. It has been implemented here at the suggestion
# of the Brandmeister team as a way to legitimately connect HBlink to the
# Brandemiester network.
# It is recommended to name the system the ID of the Brandmeister server that
# it connects to, but is not necessary. TARGET_IP and TARGET_PORT are of the
# Brandmeister or IPSC2 server you are connecting to. PASSPHRASE is the password
# that must be agreed upon between you and the operator of the server you are
# connecting to. NETWORK_ID is a number in the format of a DMR Radio ID that
# will be sent to the other server to identify this connection.
# other parameters follow the other system types.
#
# ACLs:
# OpenBridge does not 'register', so registration ACL is meaningless.
# OpenBridge passes all traffic on TS1, so there is only 1 TGID ACL.
# Otherwise ACLs work as described in the global stanza
[OBP-TEST]
MODE: OPENBRIDGE
ENABLED: False
IP:
PORT: 62035
NETWORK_ID: 0
PASSPHRASE: password
TARGET_IP: 1.2.3.4
TARGET_PORT: 62035
USE_ACL: True
SUB_ACL: DENY:1
TGID_ACL: PERMIT:ALL
RELAX_CHECKS: True
ENHANCED_OBP: True
PROTO_VER: 5

# MASTER INSTANCES - DUPLICATE SECTION FOR MULTIPLE MASTERS
# HomeBrew Protocol Master instances go here.
# IP may be left blank if there's one interface on your system.
# Port should be the port you want this master to listen on. It must be unique
# and unused by anything else.
# Repeat - if True, the master repeats traffic to peers, False, it does nothing.
#
# MAX_PEERS -- maximun number of peers that may be connect to this master
# at any given time. This is very handy if you're allowing hotspots to
# connect, or using a limited computer like a Raspberry Pi.
#
# ACLs:
# See comments in the GLOBAL stanza

######################################################################################
#                                                                                    #
#                                      MASTERS                                       #
#                                                                                    #
######################################################################################
[SYSTEM]
MODE: MASTER
ENABLED: True
REPEAT: True
MAX_PEERS: 1
EXPORT_AMBE: False
IP: 
PORT: 54000
PASSPHRASE: passw0rd
GROUP_HANGTIME: 5
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
DEFAULT_UA_TIMER: 60
SINGLE_MODE: True
VOICE_IDENT: False
TS1_STATIC:
TS2_STATIC:
DEFAULT_REFLECTOR: 0
ANNOUNCEMENT_LANGUAGE: en_GB
GENERATOR: 100
ALLOW_UNREG_ID: True
PROXY_CONTROL: False
OVERRIDE_IDENT_TG:

######################################################################################
#                                                                                    #
#                                       PARROT                                       #
#                                                                                    #
######################################################################################
[PARROT]
MODE: PEER
ENABLED: True
LOOSE: False
EXPORT_AMBE: False
IP:  
PORT: 54916
MASTER_IP: 127.0.0.1
MASTER_PORT: 54915
PASSPHRASE: passw0rd
CALLSIGN: PARROT
RADIO_ID: 234018999
RX_FREQ: 449000000
TX_FREQ: 444000000
TX_POWER: 25
COLORCODE: 1
SLOTS: 2
LATITUDE: 00.0000
LONGITUDE: 000.0000
HEIGHT: 75
LOCATION: TG9990
DESCRIPTION: PARROT
URL: www.freestar.network
SOFTWARE_ID: 20170620
PACKAGE_ID: SYSTEM-X
GROUP_HANGTIME: 5
OPTIONS:
USE_ACL: True
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
ANNOUNCEMENT_LANGUAGE: en_GB

######################################################################################
#                                                                                    #
#                           DMR-PEERS  (Ports 54100 - 54199)                         #
#                                                                                    #
######################################################################################
[DMR+/TG1]
MODE: PEER
ENABLED: False
LOOSE: True
EXPORT_AMBE: False
IP:
PORT: 54100
MASTER_IP: 111.222.333.444
MASTER_PORT: 12345
PASSPHRASE: passw0rd
CALLSIGN: M0VUB-L
RADIO_ID: 234587501
RX_FREQ: 449000000
TX_FREQ: 444000000
TX_POWER: 25
COLORCODE: 1
SLOTS: 2
LATITUDE: 00.0000
LONGITUDE: 000.0000
HEIGHT: 75
LOCATION: Nottingham, UK
DESCRIPTION: SYSTEM-X Link GB
URL: freestar.network
SOFTWARE_ID: 20170620
PACKAGE_ID: MMDVM_SYSTEM-X
GROUP_HANGTIME: 5
OPTIONS: TS2_1=1;
USE_ACL: True
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
ANNOUNCEMENT_LANGUAGE: en_GB

######################################################################################
#                                                                                    #
#                           XLX-PEERS  (Ports 54200 - 54299)                         #
#                                                                                    #
######################################################################################
[XLX-248-A]
MODE: XLXPEER
ENABLED: False
LOOSE: True
EXPORT_AMBE: False
IP:
PORT: 54213
MASTER_IP: xlx248.freestar.network
MASTER_PORT: 62030
PASSPHRASE: passw0rd
CALLSIGN: M0VUB
RADIO_ID: 2340189
RX_FREQ: 449000000
TX_FREQ: 444000000
TX_POWER: 25
COLORCODE: 1
SLOTS: 2
LATITUDE: 38.0000
LONGITUDE: -095.0000
HEIGHT: 75
LOCATION: System-X
DESCRIPTION: Module A
URL: www.freestar.network
SOFTWARE_ID: 20170620
PACKAGE_ID: MMDVM_SYSTEM-X
GROUP_HANGTIME: 5
# 4000 + the numerical position of the module in the alphabet - e.g A = 4001
XLXMODULE: 4001
USE_ACL: True
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
ANNOUNCEMENT_LANGUAGE: en_GB

# End of RYSEN MASTER+ Configuration file
EOF

        echo Install /etc/rysen/rules.py ...
cat << EOF > /etc/rysen/rules.py
'''
RYSEN DMRMaster+ Version 1.3.8

THIS EXAMPLE WILL NOT WORK AS IT IS - YOU MUST SPECIFY YOUR OWN VALUES!!!

This file is organized around the "Conference Bridges" that you wish to use. If you're a c-Bridge
person, think of these as "bridge groups". You might also liken them to a "reflector". If a particular
system is "ACTIVE" on a particular conference bridge, any traffid from that system will be sent
to any other system that is active on the bridge as well. This is not an "end to end" method, because
each system must independently be activated on the bridge.

The first level (e.g. "FREESTAR" or "FREESTAR UK" in the examples) is the name of the conference
bridge. This is any arbitrary ASCII text string you want to use. Under each conference bridge
definition are the following items -- one line for each HBSystem as defined in the main HBlink
configuration file.

    * SYSTEM - The name of the sytem as listed in the main hblink configuration file (e.g. hblink.cfg)
        This MUST be the exact same name as in the main config file!!!
    * TS - Timeslot used for matching traffic to this confernce bridge
        XLX connections should *ALWAYS* use TS 2 only.
    * TGID - Talkgroup ID used for matching traffic to this conference bridge
        XLX connections should *ALWAYS* use TG 9 only.
    * ON and OFF are LISTS of Talkgroup IDs used to trigger this system off and on. Even if you
        only want one (as shown in the ON example), it has to be in list format. None can be
        handled with an empty list, such as " 'ON': [] ".
    * TO_TYPE is timeout type. If you want to use timers, ON means when it's turned on, it will
        turn off afer the timout period and OFF means it will turn back on after the timout
        period. If you don't want to use timers, set it to anything else, but 'NONE' might be
        a good value for documentation!
    * TIMOUT is a value in minutes for the timout timer. No, I won't make it 'seconds', so don't
        ask. Timers are performance "expense".
    * RESET is a list of Talkgroup IDs that, in addition to the ON and OFF lists will cause a running
        timer to be reset. This is useful   if you are using different TGIDs for voice traffic than
        triggering. If you are not, there is NO NEED to use this feature.
'''

#start of rules
BRIDGES = {

##########################################################################################################################################################
#                                                                                                                                                        #
#                                                                  PARROT                                                                                #
#                                                                                                                                                        #
##########################################################################################################################################################
    '9990': [
            {'SYSTEM': 'PARROT',  'TS': 2, 'TGID': 9990,   'ACTIVE': True, 'TIMEOUT': 15, 'TO_TYPE': 'NONE',  'ON': [], 'OFF': [], 'RESET': []},

        ]

#end of rules
}
EOF
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Set up logging....."
echo "------------------------------------------------------------------------------"
        mkdir -p /var/log/rysen
        touch /var/log/rysen/rysen.log
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Installing docker-compose YAML and set up to run the server....."
echo "------------------------------------------------------------------------------"
sleep 2
        cd $INSDIR
        git clone $FDGITXREPO
        cp RYSEN/docker-configs/scripts/docker-compose-systemx.yml /etc/rysen/docker-compose.yml
        
if [ -e /etc/rysen/docker-compose.yml ]
then
        echo "----------------------------------------------------------------------------------------------"
        echo "It looks like the docker-compose file installed correctly. The installation will now proceed. "
        echo "----------------------------------------------------------------------------------------------"
        else
        echo "-----------------------------------------------------------------------------------------------"
        echo "I dont see the docker-compose file! Please check your configuration and try again. Exiting....."
        echo "-----------------------------------------------------------------------------------------------"
        exit 0
fi
echo "Done"
sleep 2
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Setting up scripts for /etc/rysen. RYSEN control scripts....."
echo "------------------------------------------------------------------------------"
sleep 2
        cd $INSDIR
        cp -a RYSEN/docker-configs/scripts/. /etc/rysen/
        
if [ -e /etc/rysen/start.sh ]
then
        echo "----------------------------------------------------------------------------------------------"
        echo "It looks like the docker-compose file installed correctly. The installation will now proceed. "
        echo "----------------------------------------------------------------------------------------------"
        else
        echo "-----------------------------------------------------------------------------------------------"
        echo "I dont see the docker-compose file! Please check your configuration and try again. Exiting....."
        echo "-----------------------------------------------------------------------------------------------"
        exit 0
fi
echo "Done"
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Set up permissions....."
echo "------------------------------------------------------------------------------"
        chmod -R 755 /etc/rysen
        chmod -R 777 /etc/rysen/json
        chown -R 54000 /etc/rysen
        chown -R 54000 /var/log/rysen
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Wake up the docker container and pull latest docker image from ShaYmez....."
echo "------------------------------------------------------------------------------"
        cd $FDDIR
        docker-compose up -d
        sleep 10
        docker-compose down
echo "Done."
sleep 2
echo "Stopping container....."
sleep 2
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Finishing up.....        Cleaning up installation files.....     /opt/tmp....."
echo "------------------------------------------------------------------------------"
        rm -rf /opt/tmp
echo "Done."
sleep 2
# SSL HTTPS Install / Remove for public install
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Certbot installation for SSL / HTTPS"
echo "------------------------------------------------------------------------------"
        snap install core
        snap refresh core
        apt remove certbot
        snap install --classic certbot
        ln -s /snap/bin/certbot /usr/bin/certbot
echo ""
echo ""
echo "------------------------------------------------------------------------------"
echo "Install the certificate. Please follow the prompts."
echo "------------------------------------------------------------------------------"
        sleep 3
        certbot --apache
        a2enmod proxy proxy_http proxy_wstunnel
        systemctl restart apache2
sleep 2        
echo "Done."        
echo ""
echo ""
echo "----------------------------------------------------------------------------------"
echo "The installation will now complete.... Please wait.... Starting docker engine....."
echo "----------------------------------------------------------------------------------"
sleep 5
        sleep 10
        clear
        sleep 2
echo "Starting RYSEN DMRMaster+....."
        sleep 5
        cd $FDDIR
        docker-compose up -d
        sleep 5
figlet "RYSEN."
sleep 3
        docker ps
        echo "Sanity Check....."
        sleep 5
        docker container logs systemx
echo "Done."
sleep 2
echo "Starting RYSEN Dashboard....."
        systemctl enable hbmon
        systemctl start hbmon
figlet "RYSEN-DASH."
echo "Done."
sleep 2
echo ""
echo ""
echo "*************************************************************************"
echo ""
echo "            The RYSEN DMRMaster+ Installation Is Complete!               "
echo ""
echo "                ******* Now reboot the server. *******                   "
echo ""
echo "         Use 'docker container logs rysen' to check the status.          "
echo "                  logs are part in /var/log/rysen.                       "
echo "  Just make sure this computer can be accessed over UDP specified port   "
echo "  You will need to edit your config and then run the following command   "
echo ""
echo "                           cd /etc/rysen                                 "
echo "                         docker-compose up -d                            "
echo "        More documentation can be found on the RYSEN git repo            "
echo "                  https://github.com/ShaYmez/RYSEN                       "
echo ""
echo "                     Your IP address is $LOCAL_IP                        "
echo ""
echo "              Your running on $ARC with Debian $VERSION                  "
echo ""           
echo "                     Thanks for using this script.                       "
echo "                 Copyright © 2022 Shane Daley - M0VUB                    "
echo "           More information can be found @ https://rysen.uk              "
echo ""
echo "*************************************************************************"

