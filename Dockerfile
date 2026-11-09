FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
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
