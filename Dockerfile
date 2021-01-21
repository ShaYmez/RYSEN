FROM python:3.7-slim-buster
#FROM python:3.7-alpine

COPY entrypoint /entrypoint

RUN useradd -u 54000 radio && \
        apt-get update && \
        apt-get install -y git gcc && \
        cd /opt && \
        git clone https://github.com/hacknix/freedmr && \
        cd freedmr && \
        pip install --no-cache-dir -r requirements.txt && \
        apt-get purge  -y git gcc libx11-6 && \
	apt-get clean -y && \
	apt-get autoremove -y && \
        rm -rf /var/lib/apt/lists/* && \
        chown -R radio: /opt/freedmr

EXPOSE 54000

USER radio

ENTRYPOINT [ "/entrypoint" ]
