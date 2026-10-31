FROM python:3.12-slim-bookworm
# p7zip-full alone cannot open RAR; p7zip-rar (Debian non-free) adds the RAR codec.
RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends p7zip-full p7zip-rar ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY ch_web.py library_core.py ./
COPY web ./web
RUN useradd --uid 1000 --create-home chmanager && mkdir /data && chown chmanager:chmanager /data
USER chmanager
VOLUME ["/data"]
EXPOSE 8899
# The default allowed hosts include 127.0.0.1, which is what this probe sends.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8899/', timeout=4)"
CMD ["python3", "ch_web.py", "--data-dir", "/data", "--host", "0.0.0.0"]
