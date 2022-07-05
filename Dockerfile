FROM python:3.9.4-alpine3.13
WORKDIR /srv

RUN addgroup -S uwsgi && adduser -S uwsgi -G uwsgi

RUN apk add --no-cache python3 \
    uwsgi \
    ca-certificates \
    gcc \
    git \
    g++ \
    linux-headers \
    libxml2-dev \
    libxslt-dev \
    python3-dev \
    uwsgi-python3 \
    jpeg-dev \
    tzdata

ENV TZ=Europe/Berlin
RUN ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

COPY requirements.txt ./
RUN pip3 install --upgrade pip
RUN pip3 install --no-cache-dir -r requirements.txt

ARG envconfig
ENV env=$envconfig

COPY . /srv/

USER uwsgi

CMD [ "uwsgi", "--master", "/srv/configs/uwsgi_docker.ini" ]

EXPOSE 9090
