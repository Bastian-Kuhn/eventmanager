FROM python:3.10-alpine3.17
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
    libffi-dev \
    tzdata

ENV TZ=Europe/Berlin
RUN ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

COPY requirements.txt ./
RUN pip3 install --upgrade pip
RUN pip3 install --no-cache-dir -r requirements.txt
# uWSGI is the prod server, not an app/library dependency, so it is kept out of
# requirements.txt (and thus the published wheel). It must be the pip build so
# the uwsgi binary embeds the same interpreter pip installed the deps into;
# otherwise the apk uwsgi-python3 plugin loads Alpine's system Python, which
# lacks these packages (ModuleNotFoundError on the first third-party import).
RUN pip3 install --no-cache-dir uWSGI==2.0.22

ARG envconfig
ENV env=$envconfig

COPY . /srv/

USER uwsgi

CMD [ "uwsgi", "--master", "/srv/configs/uwsgi_docker.ini" ]

EXPOSE 9090
