# HotSpot Proxy V2 #

The Hotspot Proxy is a protocol-aware UDP proxy for Homebrew Protocol that can distribute connections arriving on a single UDP port to a range of UDP ports on the backend server. It is used to support a single port for clients to connect to but still work with the one port, one system model originally designed into HBLink. 

The proxy uses the DMR ID embedded in every HBP packet to track the connection.

The proxy is included in the Docker image, so there's no need to set this up manually of you use Docker.  

Using the proxy is simple. 

First you need to create a number of entries for your hotspots to use, with sequential port numbers. Please see the GENERATOR config file
option for this.

In the file hotspot_proxy_v2.py, you will find, towards the bottom, some configuration options. Edit these to suit your system. 

*run the proxy:*

`python3 ./hotspot_proxy_v2.py`

*Credits:*
Simon G7RZU