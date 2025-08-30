FROM python:3.12-slim

WORKDIR /app

# Install system deps (if any) and Python deps
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py

# Run the fetch loop
CMD ["python", "-u", "app.py"]
