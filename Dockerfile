# Official Playwright Python image — has ALL Chromium system deps pre-installed.
# No apt-get needed, no timeouts, no hash mismatches.
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --prefer-binary -r requirements.txt

# Install only the Chromium browser binary (deps already in base image)
RUN playwright install chromium

# Copy source code
COPY . .

CMD ["python", "worker.py"]
