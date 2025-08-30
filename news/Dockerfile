FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies (no pip self-upgrade)
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# App code
COPY app.py .

CMD ["python", "-u", "app.py"]

