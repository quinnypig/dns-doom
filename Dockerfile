FROM python:3.15-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends bind9 bind9-utils bind9-dnsutils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# pip is only needed at build time; its vendored urllib3/msgpack carry CVEs
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip
COPY . .
ENTRYPOINT ["python", "-m", "dnscpu"]
CMD ["demo"]
