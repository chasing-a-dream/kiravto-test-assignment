FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kiravto ./kiravto
RUN useradd -m app && mkdir -p /app/data && chown -R app:app /app
USER app
CMD ["python", "-m", "kiravto", "api"]
