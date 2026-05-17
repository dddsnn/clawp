FROM python:3.14-slim AS build
WORKDIR /build
RUN apt-get update && \
    apt-get install -y build-essential libolm3 && \
    apt-get clean
COPY src src
COPY LICENSE pyproject.toml README.md uv.lock ./
RUN pip wheel -w wheels .

FROM python:3.14-slim AS app
WORKDIR /app
RUN apt-get update && \
    apt-get install -y libolm3 && \
    apt-get clean
COPY --from=build /build/wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -rf /tmp/ && pip cache purge
USER 1000:1000
