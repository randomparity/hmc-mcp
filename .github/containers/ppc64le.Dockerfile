FROM ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        build-essential \
        git \
        just=1.21.0-1 \
        libssl-dev \
        pkg-config \
        python3 \
        python3-dev \
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

ARG RUSTUP_VERSION=1.29.0
ARG RUSTUP_SHA256=4bfff85bd3967d988e14567aa9cc6ab0ea386f0ffeff0f9f14d23f0103bf1f97
ARG RUST_VERSION=1.97.1

ENV PATH="/root/.cargo/bin:${PATH}"

RUN rustup_init="rustup-init" \
    && curl --fail --location --proto '=https' --tlsv1.2 \
        --output "${rustup_init}" \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/powerpc64le-unknown-linux-gnu/rustup-init" \
    && echo "${RUSTUP_SHA256}  ${rustup_init}" | sha256sum --check --strict \
    && chmod 0755 "${rustup_init}" \
    && ./"${rustup_init}" --no-modify-path --profile minimal \
        --default-toolchain "${RUST_VERSION}" --default-host powerpc64le-unknown-linux-gnu -y \
    && rm "${rustup_init}"

WORKDIR /workspace

CMD ["bash", "-euo", "pipefail", "-c", \
    "test \"$(uname -m)\" = \"ppc64le\" && git config --global --add safe.directory /workspace && just setup && just verify"]
