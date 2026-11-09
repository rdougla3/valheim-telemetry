FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl tar unzip python3 systemd libatomic1 libstdc++6 \
    libpulse0 libpulse-mainloop-glib0 libcurl4t64 lib32gcc-s1 lib32stdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY valheim/*.sh /opt/valheim/
COPY valheim-dashboard/*.py /opt/valheim-dashboard/
COPY valheim-dashboard/static /opt/valheim-dashboard/static
ENV HOME=/opt/valheim/home PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /opt/valheim
CMD ["/bin/bash", "/opt/valheim/start-server.sh"]
