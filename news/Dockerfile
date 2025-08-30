FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# minimal tools to fetch get-pip
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && curl -sSLo /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py \
 && python /tmp/get-pip.py "pip==25.0.1" \
 && rm -rf /var/lib/apt/lists/* /tmp/get-pip.py

# deps
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
 && python -m pip install --no-cache-dir --force-reinstall influxdb-client==1.49.0 \
 && python -c "import importlib.util as u; assert u.find_spec('influxdb_client'); print('OK: influxdb_client present')"

# app
COPY app.py ./app.py
CMD ["python","-u","app.py"]
