FROM python:3.15-slim@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0
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
