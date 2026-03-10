# ─────────────────────────────────────────────────────────────────────────────
# CUA FastAPI Server – Python 3.12 + Playwright Chromium
# Uses the official Playwright base image so all browser deps are pre-installed.
# ─────────────────────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

# Non-interactive apt installs
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install Python dependencies first (layer-cache friendly)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are already installed in the base image.
# Run a quick verify so CI fails fast if the image is wrong.
RUN python -m playwright install --dry-run chromium

# Copy application source
COPY . .

# Runtime configuration
ENV PORT=8501
EXPOSE 8501

# Uvicorn entry point
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8501"]
