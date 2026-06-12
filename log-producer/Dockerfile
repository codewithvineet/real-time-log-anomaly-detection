# Using slim image to keep the container small
FROM python:3.10-slim

WORKDIR /app

# Install dependencies first (Docker layer caching — only reinstalls if requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY producer.py .

# Run the producer
CMD ["python", "producer.py"]
