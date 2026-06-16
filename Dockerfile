FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 8000
# Web UI demo: open http://localhost:8000, fill in your personal tokens, click Run.
CMD ["uvicorn", "webui:app", "--host", "0.0.0.0", "--port", "8000"]
