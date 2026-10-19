FROM python:3.12-slim
WORKDIR /srv
COPY server.py .
ENV PYTHONUNBUFFERED=1
USER nobody
CMD ["python", "server.py"]
