FROM python:3.10-alpine

COPY entrypoint /entrypoint

ENTRYPOINT [ "/entrypoint" ]

RUN adduser -D -u 54000 radio && \
        apk update && \
        apk add git gcc musl-dev && \
        cd /opt && \
        git clone https://github.com/ShaYmez/RYSEN.git freedmr && \
        cd /opt/freedmr && \
        pip install --no-cache-dir -r requirements.txt && \
        apk del git gcc musl-dev && \
        chown -R radio: /opt/freedmr

USER radio

ENTRYPOINT [ "/entrypoint" ]
