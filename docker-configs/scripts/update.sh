#!/bin/bash

# SYSTEM-X FORMERLY RYSEN MASTER+ (HBlink3) A FORK OF THE FREEDMR / HBLINK PROJECT
# This script written by Shane Daley M0VUB. The script gracefully shutsdown services while services are cleaned and logs are truncated.
# We can also add items in this script for future use like updates or further log trims.
# Add to the cron tab for auto execution

#   Copyright (C) 2020 Shane P, Daley  M0VUB <support@gb7nr.co.uk>
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

# Update containers / service clean/truncate/ and restart of services..

LOCAL_IP=$(ip a | grep inet | grep "eth0\|en" | awk '{print $2}' | tr '/' ' ' | awk '{print $1}')
ARC=$(lscpu | grep Arch | awk '{print $2}')
VERSION=$(sed 's/\..*//' /etc/debian_version)

clear

echo Starting update.....
sleep 1
echo "."
sleep 1
echo ".."
sleep 1
echo "..."
sleep 1
echo "....."
echo Stopping System-X.....
docker-compose down
echo Removing all docker images.....
docker rmi $(docker images -q -a) --force
figlet "ShaYmez." 
# echo Removing old containers.....
# docker container rm systemx --force 
# echo Installing new containers.....
# docker run --name=systemx -d --read-only -v /etc/freedmr/freedmr.cfg:/opt/freedmr/freedmr.cfg \
# -v /var/log/freedmr/freedmr.log:/opt/freedmr/freedmr.log \
# -v /etc/freedmr/rules.py:/opt/freedmr/rules.py -p 62031:62031/udp -p 62034-62046:62034-62046/udp \
# -p 4321:4321/tcp hacknix/freedmr:latest
sleep 1
echo Flushing services and restarting.....
./flush.sh
sleep 2        
echo "Done."        
echo ""
echo ""
echo "*************************************************************************"
echo ""
echo "                     The System-X Update Is Complete!                    "
echo ""
echo "               ******* To Upgrade run ./upgrade.sh *******               "
echo ""
echo "        Use 'docker container logs systemx' to check the status.         "
echo "                   logs are part in /var/log/freedmr.                    "
echo "  Just make sure this computer can be accessed over UDP specified port   "
echo "  You will need to edit your config and then run the following command   "
echo ""
echo "                            cd /etc/rysen                                "
echo "                         docker-compose up -d                            "
echo "       More documentation can be found on the System-X git repo          "
echo "                 https://github.com/ShaYmez/System-X                     "
echo ""
echo "                     Your IP address is $LOCAL_IP                        "
echo ""
echo "              Your running on $ARC with Debian $VERSION                  "
echo ""           
echo "                     Thanks for using this script.                       "
echo "                Copyright © 2022 Shane Daley - M0VUB                     "
echo "   More information can be found @ https://freestar.network/development  "
echo ""
echo "*************************************************************************"