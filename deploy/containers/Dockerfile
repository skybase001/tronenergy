FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY packages ./packages
COPY services ./services
COPY migrations ./migrations
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps -e . \
    && useradd --create-home --uid 10001 app
USER app
CMD ["uvicorn", "tronenergy_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
