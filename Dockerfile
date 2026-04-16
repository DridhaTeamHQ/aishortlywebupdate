# Use the official Playwright Python image — ALL Chromium deps pre-installed.
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --prefer-binary -r requirements.txt

# Install Chromium + ensure all system deps are present using Playwright's own tool
RUN playwright install --with-deps chromium

# Copy source
COPY . .

CMD ["python", "worker.py"]
