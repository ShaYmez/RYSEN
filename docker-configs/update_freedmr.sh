#!/bin/bash

echo updating FreeDMR to latest docker build

docker pull hacknix/freedmr:development-latest && 
docker container rm freedmr --force &&
docker run -d --name freedmr  --read-only  -v  /etc/freedmr/freedmr.cfg:/opt/freedmr/freedmr.cfg -v /var/log/freedmr/freedmr.log:/opt/freedmr/freedmr.log -v /etc/freedmr/rules.py:/opt/freedmr/rules.py -p 62031:62031/udp -p 62045:62045/udp -p 4321:4321/tcp hacknix/freedmr:latest &&
conntrack -F

echo Done

