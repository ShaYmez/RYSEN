#!/bin/bash

docker pull hacknix/freedmr:latest && 
docker container rm freedmr --force &&
docker run --name=freedmr -d --read-only -v /etc/freedmr/freedmr.cfg:/opt/freedmr/freedmr.cfg \
-v /var/log/freedmr/freedmr.log:/opt/freedmr/freedmr.log \
-v /etc/freedmr/rules.py:/opt/freedmr/rules.py -p 62031:62031/udp -p 62036-62046:62036-62046/udp \
-p 4321:4321/tcp hacknix/freedmr:latest
conntrack -F
