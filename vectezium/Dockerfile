# syntax=docker/dockerfile:1
# Vectezium, built from source: the `vectezium` branch of https://github.com/yokunade/Vectezium (a
# fork of Vector v0.60.0 with the mysql_cdc and postgres_cdc sources), pinned to a commit. The
# first build compiles Vector and takes 10-30 minutes depending on the machine; later builds reuse
# the cargo cache.
#
# Features: the set a real deployment needs (MySQL and PostgreSQL CDC sources, Azure Blob sink,
# jemalloc) plus the file sink, which the demo uses to feed the live view in the UI.

ARG VECTEZIUM_REPO=https://github.com/yokunade/Vectezium.git
ARG VECTEZIUM_COMMIT=c80efbb106c587f74623ce0433ed3cf1fd129498

FROM rust:1.96.1-trixie@sha256:1f0dbad1df66647807e6952d1db85d0b2bda7606cb2139d82517e4f009967376 AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends clang cmake libclang-dev protobuf-compiler \
 && rm -rf /var/lib/apt/lists/*
ARG VECTEZIUM_REPO
ARG VECTEZIUM_COMMIT
WORKDIR /src/vectezium
RUN git init -q . \
 && git remote add origin "$VECTEZIUM_REPO" \
 && git fetch -q --depth 1 origin "$VECTEZIUM_COMMIT" \
 && git checkout -q FETCH_HEAD
# Thin LTO with 16 codegen units: much faster to build than Vector's fat LTO, same throughput.
ENV CARGO_PROFILE_RELEASE_LTO=thin \
    CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
ARG FEATURES=sources-mysql_cdc,sources-postgres_cdc,sinks-azure_blob,sinks-file,tikv-jemallocator
RUN --mount=type=cache,id=vectezium-cargo-registry,target=/usr/local/cargo/registry \
    --mount=type=cache,id=vectezium-target,target=/src/vectezium/target \
    cargo build --release --locked --no-default-features --features "$FEATURES" \
 && cp target/release/vector /vectezium

# The licence files the image carries (MPL-2.0 section 3.2): the licence, the NOTICE for this
# modified version, and the third-party crate list, which includes the crates the CDC sources add.
# The source is the fork at the commit above.
RUN mkdir -p /doc \
 && cp LICENSE NOTICE LICENSE-3rdparty.csv /doc/ \
 && echo "${VECTEZIUM_REPO%.git}/tree/${VECTEZIUM_COMMIT}" > /doc/SOURCE \
 && chmod 644 /doc/*

# Empty, writable directories for state, the live event files and the signal folders, owned by
# nonroot (the runtime image has no shell to create them).
FROM gcr.io/distroless/cc-debian13:debug-nonroot@sha256:1b89bf5fd2bce2d4bd25bcf083c6da90178d2cd06bfb75dc3a66bc5c3eb6c3e7 AS dirs
SHELL ["/busybox/sh", "-c"]
RUN mkdir -p /tmp/out/var/lib/vectezium /tmp/out/var/log/vectezium /tmp/out/signals

# A minimal runtime base: glibc, CA certificates, no shell, runs as nonroot (65532).
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:3ee00291cf386ec52a552e6a858d555debd74776eb4bfa53fa3df2cd6677515e
ARG VECTEZIUM_COMMIT
COPY --from=dirs --chown=65532:65532 /tmp/out/var/lib/vectezium /var/lib/vectezium
COPY --from=dirs --chown=65532:65532 /tmp/out/var/log/vectezium /var/log/vectezium
COPY --from=dirs --chown=65532:65532 /tmp/out/signals /signals
COPY --from=build /vectezium /usr/local/bin/vectezium
COPY --from=build /doc /usr/share/doc/vectezium
LABEL org.opencontainers.image.title="Vectezium" \
      org.opencontainers.image.description="Vector v0.60.0 with the mysql_cdc and postgres_cdc sources" \
      org.opencontainers.image.licenses="MPL-2.0" \
      org.opencontainers.image.authors="yokunade" \
      org.opencontainers.image.source="https://github.com/yokunade/Vectezium" \
      org.opencontainers.image.revision="${VECTEZIUM_COMMIT}"
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/vectezium"]
