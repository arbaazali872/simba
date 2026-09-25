FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Install build dependencies for Alpine
RUN apk add --no-cache build-base musl-dev libffi-dev rust cargo gettext

COPY requirements.txt /code/
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . /code/
# settings.py writes to logs/django_errors.log, but git doesn't track the empty
# logs/ folder, so create it before any manage.py command runs
RUN mkdir -p /code/logs
COPY entrypoint.sh /code/
# A Windows checkout (git core.autocrlf=true) gives entrypoint.sh CRLF line endings,
# and sh then fails with "set: illegal option -". dos2unix makes it LF; it's a no-op
# on files that are already LF (Linux/macOS checkouts).
# Installed explicitly (not relying on busybox) and kept on its own line so changing it
# doesn't invalidate the slow pip install cache. On a Debian-based image (e.g.
# python:3.13-slim) use: apt-get update && apt-get install -y dos2unix
RUN apk add --no-cache dos2unix
RUN dos2unix /code/entrypoint.sh && chmod +x /code/entrypoint.sh

RUN python manage.py compilemessages

RUN python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["sh", "/code/entrypoint.sh"]
