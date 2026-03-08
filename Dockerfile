FROM debian:bookworm-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

COPY fact/ /opt/fact/fact/
COPY setup.py /opt/fact/setup.py

RUN python3 -m pip install --no-cache-dir --target /opt/python /opt/fact && \
    rm -rf /opt/fact

FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        gcc libc6-dev make \
        libclang1-14 libclang-common-14-dev \
        sudo && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/python /opt/python

ENV PYTHONPATH=/opt/python

# UserID 5000 required for Artemis Build Infrastructure
RUN useradd -m --uid 5000 artemis_user && \
    echo "artemis_user ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers

# Change the user to the default Artemis user (away from root)
USER artemis_user
WORKDIR /home
