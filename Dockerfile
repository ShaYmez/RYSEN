FROM debian:11.5

COPY entrypoint /entrypoint

ENTRYPOINT [ "/entrypoint" ]

RUN useradd -D -u 54000 radio && \
        apt update && \
        apt upgrade && \
        apt install git gcc g++ python3-dev libffi-dev openssl-dev musl-dev && \
        pip install --upgrade pip && \
        pip install MySQL-python && \
        pip install service-identity && \
        cd /opt && \
        git clone https://github.com/ShaYmez/RYSEN.git rysen && \
        cd /opt/rysen && \
        pip install --no-cache-dir -r requirements.txt && \
        chown -R radio: /opt/rysen

USER radio

ENTRYPOINT [ "/entrypoint" ]
