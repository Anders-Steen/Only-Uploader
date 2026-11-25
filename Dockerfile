# Build bdinfo-rs in a throwaway stage, so the final image ships one static
# binary instead of the .NET/mono runtime plus the BDInfo.exe DLLs.
FROM rust:1-slim-bookworm AS bdinfo

# No --locked here: that flag only applies to a local Cargo.lock, which
# crates.io installs don't ship. --version pins the release instead.
RUN cargo install bdinfo-rs --version 5.0.0 --root /bdinfo


FROM python:3.12-slim-bookworm

# System dependencies: ffmpeg for screenshots, mediainfo for pymediainfo
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo && \
    rm -rf /var/lib/apt/lists/*

COPY --from=bdinfo /bdinfo/bin/bdinfo-rs /usr/local/bin/bdinfo-rs

# Set up a virtual environment to isolate our Python dependencies
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Install wheel and other Python dependencies
RUN pip install --no-cache-dir --upgrade pip wheel

# Set the working directory in the container
WORKDIR /Only-Uploader

# Copy the Python requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code
COPY . .

# Unbuffered stdout/stderr so progress and tracebacks appear in real time
ENV PYTHONUNBUFFERED=1

# Set the entry point for the container
ENTRYPOINT ["python", "/Only-Uploader/upload.py"]
