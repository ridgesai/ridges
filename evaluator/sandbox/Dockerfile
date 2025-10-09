# NOTE: This Dockerfile is used to build the Docker image that will be used to run the sandbox.
#       To build the image, run:
#           docker build -t sandbox-image .
#
#       You don't need to build this Docker image manually anymore.
#       Using a SandboxManager will automatically build and use the image for you.

FROM python:3.11-slim

# Install system dependencies in a single layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends git patch diffutils && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements file for better caching
COPY sandbox_requirements.txt /tmp/sandbox_requirements.txt

# Install all packages in a single layer with optimizations
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/sandbox_requirements.txt && \
    rm /tmp/sandbox_requirements.txt

# Clean up pip cache and temporary files
RUN pip cache purge && \
    rm -rf /root/.cache/pip

# Set the working directory to /sandbox
WORKDIR /sandbox