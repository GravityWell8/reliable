# Smithery container build for the reliable MCP server.
FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[mcp]"

CMD ["python", "-m", "reliable.mcp"]
