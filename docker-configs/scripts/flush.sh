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

# Start service clean/truncate/ and restart of services.

echo "Stopping services....."
sleep 2

echo "Stopping System-X (If not already stopped)...."
docker-compose down
echo "Done."
sleep 1

echo "Stopping HBMonv2....."
systemctl stop hbmon 
echo "Done."
sleep 1

echo "System-X Flush and maintenance loop starting....."
sleep 1

echo "Starting truncate main log folder /var/log....."
truncate -s 0 /var/log/*log

echo "Starting truncate OBP entire log /var/log/rysen....."
truncate -s 0 /var/log/rysen/*log &&

echo "Starting truncate Lastheard....."
truncate -s 0 /opt/HBMonv2/log/*log

#Restart all services gracefully
echo "Restart all services....."
sleep 2

echo "Restarting docker app....."
systemctl restart docker
echo "Done."
sleep 1

echo "Restart apache2....."
systemctl restart apache2
echo "Done."
sleep 1

echo "Starting System-X....."
sleep 2
echo .
sleep 1
echo ..
sleep 1
echo ...
docker-compose up -d

sleep 1
figlet "SYSTEM-X." 
sleep 1
echo "System-X is composed....."

sleep 2
docker ps
sleep 2
echo "Checking startup error logs....."

sleep 2
docker container logs systemx 
echo "Done."

echo "Flushing network tracking table....."
conntrack -F
sleep 1

echo "Starting HBMonv2....."
systemctl restart hbmon
figlet "HBMonV2."
echo "Done."
sleep 1

echo "Done. Now Exiting....."
sleep 1
echo .
sleep 1
echo ..
sleep 1
echo ...
sleep 1
echo ....
echo "System-X is now online. Flush is complete and you may now exit. AKA ShaYmez."
#
# This script has been developed by the one and only ShaYmez. Visit https://repo.radio/ShaYmez for more super scripts!
echo "All systems have been flushed/cleaned and ready to go. Aka Dr. Node. ShaYmez, System-X"
