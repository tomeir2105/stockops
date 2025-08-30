FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && curl -sSLo /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
 && python /tmp/get-pip.py "pip==25.0.1" \
 && rm -rf /var/lib/apt/lists/* /tmp/get-pip.py

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py

CMD ["python", "-u", "app.py"]

