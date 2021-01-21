FROM python:3.7-slim-buster

COPY entrypoint /entrypoint

RUN useradd -u 54000 radio && \
        apt update && \
        apt install -y git gcc && \
        cd /opt && \
        git clone https://github.com/hacknix/freedmr && \
        cd freedmr && \
        pip install --no-cache-dir -r requirements.txt && \
        apt autoremove -y git gcc && \
        rm -rf /var/lib/apt/lists/* && \
        chown -R radio: /opt/freedmr

EXPOSE 54000

USER radio

ENTRYPOINT [ "/entrypoint" ]
