FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0


# Wird pro Service in docker-compose.yml ueberschrieben (command:).
CMD ["python3", "mcp_servers/discovery_server.py"]
