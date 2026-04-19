FROM python:3.11-slim

# Switch to Fastly CDN mirror to work around deb.debian.org 400 errors
RUN echo "deb https://deb.debian.org/debian trixie main" > /etc/apt/sources.list.d/debian.sources.tmp \
    && sed -i 's|http://deb.debian.org|https://cdn-fastly.deb.debian.org|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

RUN pip install selenium==4.18.0

RUN useradd -m -u 1000 sandbox
USER sandbox
WORKDIR /app

CMD ["python", "/scripts/script.py"]
