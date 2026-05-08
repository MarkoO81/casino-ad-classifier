FROM python:3.12-slim

WORKDIR /app

# Install Phase 1 deps only — no torch/OCR (keeps image ~150 MB)
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "app:app"]
