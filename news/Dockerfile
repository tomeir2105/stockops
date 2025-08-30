FROM python:3.12-slim

# Quality-of-life envs
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Pin pip to a safe version (avoid recent vendor breakage)
RUN python -m pip install --no-cache-dir --upgrade "pip<25.2"

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py .

CMD ["python", "-u", "app.py"]

