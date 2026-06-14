FROM python:3.10-alpine3.17
WORKDIR /srv

RUN addgroup -S app && adduser -S app -G app

RUN apk add --no-cache python3 \
    ca-certificates \
    gcc \
    git \
    g++ \
    linux-headers \
    libxml2-dev \
    libxslt-dev \
    python3-dev \
    jpeg-dev \
    libffi-dev \
    tzdata

ENV TZ=Europe/Berlin
RUN ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

COPY requirements.txt ./
RUN pip3 install --upgrade pip
RUN pip3 install --no-cache-dir -r requirements.txt
# gunicorn is the prod server, not an app/library dependency, so it is kept
# out of requirements.txt (and thus the published wheel) and installed here.
RUN pip3 install --no-cache-dir gunicorn

ARG envconfig
ENV env=$envconfig

COPY . /srv/

USER app

CMD [ "gunicorn", "--config", "/srv/configs/gunicorn.conf.py", "app:app" ]

EXPOSE 9090
