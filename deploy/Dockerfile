FROM python:3.12-slim

# System deps for kicad-cli (ERC/DRC loops)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        kicad kicad-symbols kicad-footprints \
        git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps — product layer first (cache-friendly)
COPY deploy/requirements.txt /app/deploy/requirements.txt
RUN pip install --no-cache-dir -r /app/deploy/requirements.txt

# SKiDL editable install
COPY setup.py /app/setup.py
COPY README.md /app/README.md
COPY HISTORY.md /app/HISTORY.md
COPY src/ /app/src/
RUN pip install --no-cache-dir .

# Product layer code
COPY mcp_server/ /app/mcp_server/
COPY schemas/ /app/schemas/
COPY telemetry/ /app/telemetry/
COPY deploy/schema.sql /app/deploy/schema.sql

# JLC offline data (3.8MB CSV, used by footprint resolver)
COPY corpus/jlc/jlcpcb-basic-preferred.csv /app/corpus/jlc/jlcpcb-basic-preferred.csv
COPY corpus/jlc/__init__.py /app/corpus/jlc/__init__.py
COPY corpus/jlc/lookup.py /app/corpus/jlc/lookup.py
COPY corpus/jlc/footprint_resolver.py /app/corpus/jlc/footprint_resolver.py
COPY corpus/__init__.py /app/corpus/__init__.py

ENV PYTHONPATH=/app:/app/src
ENV KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols
ENV KICAD9_FOOTPRINT_DIR=/usr/share/kicad/footprints

# Start scripts
COPY deploy/start_mcp.sh /app/deploy/start_mcp.sh
COPY deploy/start_worker.sh /app/deploy/start_worker.sh
RUN chmod +x /app/deploy/start_mcp.sh /app/deploy/start_worker.sh

ENV SERVICE_ROLE=mcp
EXPOSE 8000
CMD ["sh", "-c", "if [ \"$SERVICE_ROLE\" = 'worker' ]; then exec deploy/start_worker.sh; else exec deploy/start_mcp.sh; fi"]
