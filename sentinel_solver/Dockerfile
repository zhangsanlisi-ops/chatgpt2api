FROM ghcr.io/yukkcat/chatgpt2api:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv/sentinel_solver
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --no-shell chromium \
    && rm -rf /var/lib/apt/lists/*
COPY app.py .
EXPOSE 8080
ENTRYPOINT []
CMD ["python", "app.py"]
