## RYSEN DMRMaster+ Master Stack ##
### A fork of the HBlink3 / FreeDMR project ###

###Stable Ver 1.3.9r3 ###

RYSEN DMRMaster+ - Software to build and scale DMR Master server software. Thanks to all thats credited in code, testing and develpment of this software. This software is completly open source and is derived from the orginal fork of HBlink3 / FreeDMR. Developed in PYTHON. A number of developers have contributed to the fork and I thought the time is right to develop this version of the code completly out in the open.. as it should be. No private forks..Open Source for people to freely develop.

The base code is written by Cortney Buffington N0MJS and further develped by Simon G7RZU, Eric, K7EEL, Shane M0VUB & others.

Project under development!

More to come as development continues as a fork. - codename: RYSEN

**PROPERTY:**  
This work represents the author's interpretation of the HomeBrew Repeater Protocol, based on the 2015-07-26 documents from DMRplus, "IPSC Protocol Specs for homebrew DMR repeater" as written by Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT, also licenced under Creative Commons BY-NC-SA license.
This has been further develeped under Simon Adlem; G7RZU as the FreeDMR fork and then further develped for SystemX by Shane Daley; M0VUB under codename RYSEN.

**WARRANTY**
None. The owners of this work make absolutely no warranty, express or implied. Use this software at your own risk.

**PRE-REQUISITE KNOWLEDGE:**  
This document assumes the reader is familiar with Linux/UNIX, the Python programming language and DMR.  

**Using docker version**

Docker file included for own image build
To work with provided docker setup you will need:
* A private repository with your configuration files (all .cfg files in repo will be copyed to the application root directory on start up)
* A service user able to read your private repository (or be brave and publish your configuration, or be really brave and give your username and password to the docker)
* A server with docker installed
* Follow this simple steps:

Build your own image from source

```bash

docker build . -t shaymez/rysen:latest

```

Or user a prebuilt one in docker hub: shaymez/rysen:latest
This image is multi-arch

Wake up your container

```bash
touch /var/log/rysen.log
chown 54000 -R /var/log/rysen.log
 run -v /var/log/rysen/rysen.log:/var/log/rysen/rysen.log -e GIT_USER=$USER -e GIT_PASSWORD=$PASSWORD -e GIT_REPO=$URL_TO_REPO_WITHOUT_HTTPS://  -p 54000:54000  shaymez/rysen:latest
 ```

**MORE DOCUMENTATION TO COME**

***0x49 DE N0MJS***

Copyright (C) 2016-2020 Cortney T. Buffington, N0MJS n0mjs@me.com

This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program; if not, write to the Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
