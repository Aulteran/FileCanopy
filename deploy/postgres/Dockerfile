# PostgreSQL 17 on Alpine plus the pgvector extension (M19, optional semantic search).
#
# Built from the same base image the stack always used, so an existing data directory keeps working
# (same libc and collations; a switch to a Debian-based image would not be safe for existing data).
# pgvector is compiled from a pinned release whose tarball checksum is verified. The extension is
# only *available* here: nothing uses it until an administrator verifies a local embedding endpoint.
FROM postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b AS build
ARG PGVECTOR_VERSION=0.8.1
ARG PGVECTOR_SHA256=a9094dfb85ccdde3cbb295f1086d4c71a20db1d26bf1d6c39f07a7d164033eb4
RUN apk add --no-cache build-base wget
RUN wget -q -O /tmp/pgvector.tar.gz \
      "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" \
    && echo "${PGVECTOR_SHA256}  /tmp/pgvector.tar.gz" | sha256sum -c - \
    && mkdir /tmp/pgvector && tar -xzf /tmp/pgvector.tar.gz -C /tmp/pgvector --strip-components=1 \
    && cd /tmp/pgvector \
    && make with_llvm=no OPTFLAGS="" \
    && make with_llvm=no install DESTDIR=/out

FROM postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b
COPY --from=build /out/usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/vector.so
COPY --from=build /out/usr/local/share/postgresql/extension/ /usr/local/share/postgresql/extension/
