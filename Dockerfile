FROM python:alpine3.18

COPY entrypoint /entrypoint

ENTRYPOINT [ "/entrypoint" ]

RUN adduser -D -u 54000 radio && \
        apk update && \
        apk upgrade && \
        apk add git gcc musl-dev py3-pip && \
        pip install --upgrade pip && \
        cd /opt && \
        git clone https://github.com/ShaYmez/RYSEN.git rysen && \
        cd /opt/rysen && \
        pip install --no-cache-dir -r requirements.txt && \
        apk del git gcc musl-dev && \
        chown -R radio: /opt/rysen

USER radio

ENTRYPOINT [ "/entrypoint" ]
