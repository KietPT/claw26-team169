FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install .
EXPOSE 8080
# AgentBase Runtime expects health check on port 8080
CMD ["uvicorn", "webui:app", "--host", "0.0.0.0", "--port", "8080"]
