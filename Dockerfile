FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FORUM_DB=/data/forum.db PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py store.py manage.py AGENT_GUIDE.md ./
USER 1000:1000
EXPOSE 8080
CMD ["python", "app.py"]
