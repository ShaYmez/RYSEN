#!/bin/bash

docker run  -v -d  --read-only `pwd`/config/freedmr-docker.cfg:/opt/freedmr/hblink.cfg -v /tmp/FreeDMR.log:/opt/freedmr/freedmr.log -v `pwd`/config/rules.py:/opt/freedmr/rules.py -p 54100-54150:54100-54150/udp -p 62031:62031/udp -p 62036-62046:62036-62046/udp -p 4321:4321/tcp freedmr

