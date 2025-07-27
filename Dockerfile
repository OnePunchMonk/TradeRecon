# # FROM python:3.10-slim-bullseye

# # # WORKDIR /app

# # RUN apt-get update && apt-get install -y --no-install-recommends \
# #     librdkafka-dev \
# #     gcc \
# #     sqlite3 \
# #     libsqlite3-dev \
# #     python3-dev \
# #     && rm -rf /var/lib/apt/lists/* \
# #     && apt-get clean

# # COPY requirements.txt .

# # RUN pip install --upgrade pip && \
# #     pip install --no-cache-dir -r requirements.txt

# # COPY . .

# # EXPOSE 5000

# # # CMD ["python", "app/main.py"]

# FROM python:3.10-slim-bullseye

# # Set working directory to /app
# WORKDIR /app

# # Add /app to PYTHONPATH
# ENV PYTHONPATH="/app"

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     librdkafka-dev \
#     gcc \
#     sqlite3 \
#     libsqlite3-dev \
#     python3-dev \
#     && rm -rf /var/lib/apt/lists/* \
#     && apt-get clean

# COPY requirements.txt .

# RUN pip install --upgrade pip && \
#     pip install --no-cache-dir -r requirements.txt

# COPY . .

# EXPOSE 5000

# # You can keep CMD commented if you’re running via docker-compose
# # CMD ["python", "app/main.py"]

FROM python:3.10-slim-bullseye

# Set working directory inside container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    librdkafka-dev \
    gcc \
    sqlite3 \
    libsqlite3-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Set PYTHONPATH so Python knows where to find your app and tests
ENV PYTHONPATH=/app

# Copy requirement list and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy the full project into the container
COPY . .

# Expose service ports (adjust as needed)
EXPOSE 5000 8000

# Default command (can be overridden by docker-compose)
CMD ["python", "-m", "app.main"]

# CMD ["python", "app/main.py"]
