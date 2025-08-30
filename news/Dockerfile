FROM python:3.12-slim

WORKDIR /app

# Pin pip to avoid resolvelib ImportError issues inside slim base images
RUN pip install --no-cache-dir --upgrade "pip<25.1"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py

CMD ["python", "-u", "app.py"]
