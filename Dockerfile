FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends p7zip-full ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY ch_web.py library_core.py ./
COPY web ./web
RUN useradd --uid 1000 --create-home chmanager && mkdir /data && chown chmanager:chmanager /data
USER chmanager
VOLUME ["/data"]
EXPOSE 8899
CMD ["python3", "ch_web.py", "--data-dir", "/data", "--host", "0.0.0.0"]
