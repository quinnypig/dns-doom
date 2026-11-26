FROM python:3.15-slim@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5
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
