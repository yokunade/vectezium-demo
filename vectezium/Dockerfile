# syntax=docker/dockerfile:1
# Vectezium, built from source: Vector v0.60.0 (vectordotdev/vector, pinned to its commit) with
# mysql_cdc.patch and postgres_cdc.patch applied. The first build compiles Vector and takes 10-30
# minutes depending on the machine; later builds reuse the cargo cache.
#
# Features: the set a real deployment needs (MySQL and PostgreSQL CDC sources, Azure Blob sink,
# jemalloc) plus the file sink, which the demo uses to feed the live view in the UI.

ARG VECTOR_COMMIT=0d0b35865036f8b5c2f0104d9744fa84985ffff1

FROM rust:1.96.1-trixie@sha256:1f0dbad1df66647807e6952d1db85d0b2bda7606cb2139d82517e4f009967376 AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends clang cmake libclang-dev protobuf-compiler \
 && rm -rf /var/lib/apt/lists/*
ARG VECTOR_COMMIT
WORKDIR /src/vector
RUN git init -q . \
 && git remote add origin https://github.com/vectordotdev/vector.git \
 && git fetch -q --depth 1 origin "$VECTOR_COMMIT" \
 && git checkout -q FETCH_HEAD
COPY mysql_cdc.patch postgres_cdc.patch /tmp/
# postgres_cdc.patch builds on mysql_cdc.patch (the shared CDC core), so the order matters.
RUN git apply --whitespace=nowarn /tmp/mysql_cdc.patch \
 && git apply --whitespace=nowarn /tmp/postgres_cdc.patch
# Thin LTO with 16 codegen units: much faster to build than Vector's fat LTO, same throughput.
ENV CARGO_PROFILE_RELEASE_LTO=thin \
    CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
ARG FEATURES=sources-mysql_cdc,sources-postgres_cdc,sinks-azure_blob,sinks-file,tikv-jemallocator
RUN --mount=type=cache,id=vectezium-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=vectezium-target,target=/src/vector/target \
    cargo build --release --locked --no-default-features --features "$FEATURES" \
 && cp target/release/vector /vectezium

# The licence files the image carries (MPL-2.0 section 3.2): Vector's licence, its third-party
# crate list plus the crates the patches add, the notice for this modified version, and the
# patches themselves, which with the commit above are this version's source.
COPY NOTICE LICENSE-3rdparty-mysql_cdc.csv /tmp/licensing/
RUN mkdir -p /doc \
 && cp LICENSE /doc/LICENSE \
 && cp /tmp/licensing/NOTICE /tmp/mysql_cdc.patch /tmp/postgres_cdc.patch /doc/ \
 && { cat LICENSE-3rdparty.csv; tail -n +2 /tmp/licensing/LICENSE-3rdparty-mysql_cdc.csv; } > /doc/LICENSE-3rdparty.csv \
 && chmod 644 /doc/*

# Empty, writable directories for state and the live event file, owned by nonroot (the runtime
# image has no shell to create them).
FROM gcr.io/distroless/cc-debian13:debug-nonroot@sha256:1b89bf5fd2bce2d4bd25bcf083c6da90178d2cd06bfb75dc3a66bc5c3eb6c3e7 AS dirs
SHELL ["/busybox/sh", "-c"]
RUN mkdir -p /tmp/out/var/lib/vectezium /tmp/out/var/log/vectezium

# A minimal runtime base: glibc, CA certificates, no shell, runs as nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:3ee00291cf386ec52a552e6a858d555debd74776eb4bfa53fa3df2cd6677515e
COPY --from=dirs --chown=65532:65532 /tmp/out/var/lib/vectezium /var/lib/vectezium
COPY --from=dirs --chown=65532:65532 /tmp/out/var/log/vectezium /var/log/vectezium
COPY --from=build /vectezium /usr/local/bin/vectezium
COPY --from=build /doc /usr/share/doc/vectezium
LABEL org.opencontainers.image.title="Vectezium" \
      org.opencontainers.image.description="Vector v0.60.0 with the mysql_cdc and postgres_cdc patches" \
      org.opencontainers.image.licenses="MPL-2.0" \
      org.opencontainers.image.authors="yokunade" \
      org.opencontainers.image.source="https://github.com/yokunade/Vectezium"
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/vectezium"]
