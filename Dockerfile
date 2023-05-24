FROM python:alpine3.18

COPY entrypoint /entrypoint

ENTRYPOINT [ "/entrypoint" ]

RUN adduser -D -u 54000 radio && \
        apk update && \
        apk add git gcc musl-dev && \
        cd /opt && \
        git clone https://github.com/ShaYmez/RYSEN.git rysen && \
        cd /opt/rysen && \
        pip install --no-cache-dir -r requirements.txt && \
        apk del git gcc musl-dev && \
        chown -R radio: /opt/rysen

USER radio

ENTRYPOINT [ "/entrypoint" ]
