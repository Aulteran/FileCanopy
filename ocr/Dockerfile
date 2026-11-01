# Sealed OCR container (M11). Build: docker build -f ocr/Dockerfile ocr
# No network (a Unix socket in a volume shared with the API), read-only root: see the `ocr`
# service in deploy/docker-compose.yml. More languages: --build-arg OCR_LANG_PACKAGES="tesseract-ocr-deu"
FROM debian:13-slim
ARG OCR_LANG_PACKAGES=""
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 tesseract-ocr tesseract-ocr-eng poppler-utils ${OCR_LANG_PACKAGES} \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10003 ocr \
    && useradd --system --uid 10003 --gid 10003 --no-create-home --shell /usr/sbin/nologin ocr
# The socket directory; a named volume mounted here starts with this owner.
RUN mkdir -p /run/ocr && chown 10003:10003 /run/ocr && chmod 0755 /run/ocr
WORKDIR /srv/ocr
COPY ocr.py server.py healthcheck.py ./
USER 10003:10003
ENV OCR_SCRATCH=/tmp OCR_SOCKET=/run/ocr/ocr.sock
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD ["python3", "healthcheck.py"]
CMD ["python3", "server.py"]
