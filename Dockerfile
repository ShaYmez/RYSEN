FROM python:alpine3.18

COPY entrypoint /entrypoint

ENTRYPOINT [ "/entrypoint" ]

RUN adduser -D -u 54000 radio && \
        apk update && \
        apk upgrade && \
        apk add git gcc g++ python3-dev libffi-dev openssl-dev musl-dev && \
        pip install --upgrade pip && \
        pip install setuptools && \
        pip install msqlclient && \
        pip install service-identity && \
        cd /opt && \
        git clone https://github.com/ShaYmez/RYSEN.git rysen && \
        cd /opt/rysen && \
        pip install --no-cache-dir -r requirements.txt && \
        chown -R radio: /opt/rysen

USER radio

ENTRYPOINT [ "/entrypoint" ]
