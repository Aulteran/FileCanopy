# Sealed Office-to-PDF converter (M10, ADR-019 section 5). Build: docker build -f converter/Dockerfile converter
# No network (a Unix socket in a volume shared with the API), read-only root: see the
# `converter` service in deploy/docker-compose.yml.
FROM debian:13-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-liberation2 fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10002 converter \
    && useradd --system --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin converter
# The socket directory; a named volume mounted here starts with this owner.
RUN mkdir -p /run/converter && chown 10002:10002 /run/converter && chmod 0755 /run/converter
WORKDIR /srv/converter
COPY convert.py server.py healthcheck.py ./
USER 10002:10002
ENV CONVERTER_SCRATCH=/tmp CONVERTER_SOCKET=/run/converter/converter.sock
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["python3", "healthcheck.py"]
CMD ["python3", "server.py"]
