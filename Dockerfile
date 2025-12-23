# first image stage is the base python system
FROM python:3.13.9-slim AS base
LABEL Description="Client container for the HumeHouse PrivateIndexer swarm"


# second image stage is for dependencies and source code
FROM base AS builder

WORKDIR /app

# copy python requirements file
COPY requirements.txt /app

# install dependencies
RUN pip install -r requirements.txt


# next image stage is for runtime (non-root)
FROM base AS runner

WORKDIR /app

# set the container user
ARG UID=1000
ENV UID=${UID}

# set the container group
ARG GID=1000
ENV GID=${GID}

# copy installed python packages from the builder image
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# copy binaries from the builder image
COPY --from=builder /usr/local/bin /usr/local/bin

# copy all source code
COPY --chown=${UID}:${GID} src/ /app/src

# copy logging config
COPY --chown=${UID}:${GID} logging.yml /app

# create data directories with open permissions
RUN mkdir -m 777 /app/data \
 && chown -R ${UID}:${GID} /app/data

# set the container timezone
ARG TZ=America/Chicago
ENV TZ=${TZ}

# set up python environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# add the healthcheck to hit the app's health endpoint
HEALTHCHECK --start-period=30s --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request, sys; \
    sys.exit(0) if urllib.request.urlopen('http://localhost:8080/api/v2/health').getcode() == 200 else sys.exit(1)"

# run app as container user/group
USER ${UID}:${GID}

# open default webserver port
EXPOSE 8080

# change directories into source code for running
WORKDIR /app/src

# run the app
ENTRYPOINT ["uvicorn", "privateindexer_client.main:app", "--proxy-headers", "--workers=1", "--host=0.0.0.0", "--port=8080", "--log-config=/app/logging.yml"]