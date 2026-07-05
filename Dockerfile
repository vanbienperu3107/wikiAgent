FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wiki_agent ./wiki_agent

# APP selects which ASGI app to serve:
#   wiki_agent.app:app          → REST API (ingestion + query)
#   wiki_agent.mcp_server:app   → MCP HTTP server
ENV APP=wiki_agent.app:app
ENV PORT=8010

CMD ["sh", "-c", "uvicorn $APP --host 0.0.0.0 --port $PORT"]
