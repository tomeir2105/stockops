FROM python:3.12.5-slim-bullseye

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Pin pip to a compatible version before any installs
RUN python -m pip install --no-cache-dir --upgrade "pip<25.1"

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py .
CMD ["python", "-u", "app.py"]

