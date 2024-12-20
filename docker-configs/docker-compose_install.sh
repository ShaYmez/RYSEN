#!/bin/bash
#
###############################################################################
#   Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>  
#   Copyright (C) 2024 Shane, M0VUB <support@gb7nr.co.uk>
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

clear
echo RYSEN Master+ Docker installer...
sleep 3
echo Installing required packages...
echo Install Docker Community Edition...
apt-get -y remove docker docker-engine docker.io &&
apt-get -y update &&
apt-get -y install sudo apt-transport-https ca-certificates curl gnupg2 software-properties-common &&

sudo install -m 0755 -d /etc/apt/keyrings &&
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
sudo chmod a+r /etc/apt/keyrings/docker.asc &&

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null &&
 
apt-get -y update &&
apt-get -y install docker-ce &&

echo "Install Docker Compose..."
apt-get -y install docker-compose &&

echo "Set userland-proxy to false..."
cat <<EOF > /etc/docker/daemon.json &&
{
     "userland-proxy": false,
     "experimental": true,
     "log-driver": "json-file",
     "log-opts": {
        "max-size": "10m",
        "max-file": "3"
      }
}
EOF

echo "Restart docker..."
systemctl restart docker &&

echo "Make config directory..."
mkdir -p /etc/rysen &&
mkdir -p /etc/rysen/acme.sh && 
mkdir -p /etc/rysen/certs &&

echo "Make json directory..."
mkdir -p /etc/rysen/json &&

echo "Get config /etc/rysen/rysen.cfg ..." 
cd /etc/rysen &&
curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/config/rysen.cfg -o rysen.cfg &&

"Get rules /etc/rysen/rules.py..."
cd /etc/rysen &&
curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/config/rules.py -o rules.py &&

echo "Get docker-compose.yml..."
cd /etc/rysen &&
curl https://raw.githubusercontent.com/ShaYmez/RYSEN/refs/heads/master/docker-configs/scripts/docker-compose.yml -o docker-compose.yml &&

echo "Set perms on config directory..."
chmod -R 755 /etc/rysen &&
chown -R 54000 /etc/rysen &&

echo "Tune network stack..."
cat << EOF > /etc/sysctl.conf &&
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728                       
net.core.rmem_default=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
EOF

/usr/sbin/sysctl -p &&

echo "Create Log directory"
mkdir -p /var/log/rysen/ &&
touch /var/log/rysen/rysen.log &&
chmod -R 755 /var/log/rysen &&
chown -R 54000 /var/log/rysen &&

echo "Run RYSEN container..."
docker-compose up -d &&
docker container logs systemx &&

echo Check out docs @ https://github.com/RYSEN to understand how to implement extra functionality.
echo Setup complete!
