# NOTE: This Dockerfile is used to build the Docker image that will be used to run the sandbox.
#       To build the image, run:
#       docker build -t sandbox-runner .

FROM python:3.11-slim

# Install system dependencies in a single layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends git patch && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements file for better caching
COPY requirements.txt /tmp/requirements.txt

# Install all packages in a single layer with optimizations
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Clean up pip cache and temporary files
RUN pip cache purge && \
    rm -rf /root/.cache/pip

# Configure git to trust all sandbox directories to avoid dubious ownership warnings
RUN git config --global --add safe.directory '/sandbox/*'

CMD ["python", "/sandbox/agent_runner.py"]