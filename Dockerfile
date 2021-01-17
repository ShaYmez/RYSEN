FROM python:3.7-slim-buster

COPY entrypoint /entrypoint

RUN useradd -u 54000 radio && \
    apt update && \
    apt install -y git && \
    cd /usr/src/ && \
    git clone https://github.com/hacknix/dmr_utils && \
    cd /usr/src/dmr_utils && \
    ./install.sh && \
    rm -rf /var/lib/apt/lists/* && \
    cd /opt && \
    rm -rf /usr/src/dmr_utils && \
    git clone https://github.com/hacknix/freedmr && \
    cd /opt/freedmr/ && \
    sed -i s/.*python.*//g  requirements.txt && \
    pip install --no-cache-dir -r requirements.txt && \
    chown radio /opt/freedmr

USER radio 

EXPOSE 54000

ENTRYPOINT [ "/entrypoint" ]
