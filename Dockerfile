FROM python:3.15-slim@sha256:08a4c22c92b79ec821a7ad49768480039ee5b7accac3955228e75366e30abc8e
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
