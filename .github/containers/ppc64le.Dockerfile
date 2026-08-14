FROM ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        git \
        just=1.21.0-1 \
    && rm -rf /var/lib/apt/lists/*

ARG UV_VERSION=0.12.3
ARG UV_SHA256=bff188fcf2d867c5595f8db6061a39e54752ab213eaefc14287f37e85afe9ead

RUN archive="uv-powerpc64le-unknown-linux-gnu.tar.gz" \
    && curl --fail --location --proto '=https' --tlsv1.2 \
        --output "${archive}" \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive}" \
    && echo "${UV_SHA256}  ${archive}" | sha256sum --check --strict \
    && tar --extract --gzip --file "${archive}" \
    && install --mode 0755 uv-powerpc64le-unknown-linux-gnu/uv /usr/local/bin/uv \
    && rm --recursive "${archive}" uv-powerpc64le-unknown-linux-gnu

WORKDIR /workspace

CMD ["bash", "-euo", "pipefail", "-c", \
    "test \"$(uname -m)\" = \"ppc64le\" && just setup && just verify"]
