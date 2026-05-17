FROM rust:1 AS build-rust-mcp-filesystem
RUN mkdir /dist
RUN cargo install --locked rust-mcp-filesystem@0.4.1
RUN cp $(which which rust-mcp-filesystem) /dist

FROM python:3.14-slim AS build
WORKDIR /build
RUN apt-get update && \
    apt-get install -y build-essential libolm3 && \
    apt-get clean
COPY src src
COPY LICENSE pyproject.toml README.md uv.lock ./
RUN pip wheel -w wheels .

FROM build AS test
COPY --from=build-rust-mcp-filesystem /dist/rust-mcp-filesystem /usr/local/bin/
RUN pip install wheels/*.whl && pip cache purge
RUN pip install .[test] && pip cache purge
COPY tests tests

FROM python:3.14-slim AS app
WORKDIR /app
COPY --from=build-rust-mcp-filesystem /dist/rust-mcp-filesystem /usr/local/bin/
RUN apt-get update && \
    apt-get install -y libolm3 && \
    apt-get clean
COPY --from=build /build/wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -rf /tmp/ && pip cache purge
USER 1000:1000
