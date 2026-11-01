# Sealed Office-to-PDF converter (M10, ADR-019 section 5). Build: docker build -f converter/Dockerfile converter
# No network, no volumes, read-only root: see the `converter` service in deploy/docker-compose.yml.
FROM debian:13-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-liberation2 fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10002 converter \
    && useradd --system --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin converter
WORKDIR /srv/converter
COPY convert.py server.py ./
USER 10002:10002
ENV CONVERTER_SCRATCH=/tmp
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["python3", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8090/healthz', timeout=2)"]
CMD ["python3", "server.py"]
