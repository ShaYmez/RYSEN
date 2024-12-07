# \*\*The current recommended way of installing RYSEN Master+ is in docker. \*\*

Docker is the fastest way to having a running system, complete with proxy and echo, ready to serve repeaters and hotspots. The Docker image can be run on any system that can run Linux docker containers. We recommend Debian 11.

Not convinced? Read [Why Docker?](Why%20Docker)

# Quick Start

For a one-shot install on a Debian or Debian-based system, paste this into the terminal of a running debian-like Linux system. This will install RYSEN Master+ only. Fore the RYSEN Master+ Suite scroll down to the bottom.

*Important!*
Must be root!! for this install to work corectly! Sudo is for amateurs!

`curl https://github.com/shaymez/RYSEN/-/raw/master/docker-configs/docker-compose_install.sh | bash`

This works on Debian 10, 11, 12, PiOs (Raspbian) and recent Ubuntu systems, on other flavours, you may need to follow the process below.

The RYSEN docker image is multiarch so will work on x86, amd64 & arm64

The rest of this page details the manual install process.

## Prerequisites

Firstly, you need a system running docker. Follow the instructions for your distro [here](https://docker-docs.netlify.app/install/#server).

A change needs to be made to the docker config for openbridge to work correctly:

`echo '{ "userland-proxy": false}' > /etc/docker/daemon.json`

`systemctl restart docker`

## Grab and edit the config file

Get the file: [rysen.cfg](https://github.com/shaymez/RYSEN/-/blob/master/docker-configs/config/rysen.cfg)

`mkdir /etc/rysen`

place the rysen.cfg file in this directory.

## Make rules file

`echo "BRIDGES = {'9990': [{'SYSTEM': 'ECHO', 'TS': 2, 'TGID': 9990, 'ACTIVE': True, 'TIMEOUT': 2, 'TO_TYPE': 'NONE', 'ON': [], 'OFF': [], 'RESET': []},]}" > /etc/rysen/rules.py`

##Make JSON directory

`mkdir -p /etc/rysen/json`

## Make sure docker container can access config and rules

`chmod 755 /etc/rysen -R`

## create /etc/rysen/docker-compose.yml

Download the file here: [docker-compose.yml](https://github.com/shaymez/RYSEN/-/raw/master/docker-configs/scipts/docker-compose.yml)

## Add network tuning

Add the following to the end if /etc/sysctl.conf:

```
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728                       
net.core.rmem_default=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
```

Run command:

`sysctl -p`

\#Run RYSEN Master+

`cd /etc/rysen`

`docker-compose up`

Once you are sure it has run correctly, you can restart in the background

`docker-compose up -d`

## Stop docker container

`docker-compose down`

## Update rysen

`cd /etc/rysen`

`docker-compose down`

`docker-compose pull`

`docker-compose up -d`

## Restart the container (for example when config is changed)

`docker-compose restart`

## After rysen is updated or restarted this may be required

`conntrack -F`

This flushes the connection tracking table for NAT. Without this, you might not see traffic for a while.

For more docker commands go [here](Docker%20Commands%20Cheat%20Sheet)

## Postrequisites

*RYSEN Master+ Suite (SYSTEMX)*
You can however add the RYSEN Master+ suite, Dashboard, Whiptail menus and additional software to make a fully fletched DMR Master Server if you wish by executing our official installer [here](https://github.com/shaymez/RYSEN-Installer)

*Credits*
Simon G7RZU
Shaymez M0VUB