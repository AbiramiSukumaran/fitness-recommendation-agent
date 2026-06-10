# Use the official lightweight Python 3.12 image.
# https://hub.docker.com/_/python
FROM python:3.12-slim

# Allow statements and log messages to immediately appear in the Cloud Run logs
ENV PYTHONUNBUFFERED=True

# Set the working directory in the container
WORKDIR /app

# Copy all local files to the container image
COPY . /app

# Install production dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Cloud Run injects a dynamic $PORT environment variable. 
# Your app.py already catches this via `int(os.environ.get('PORT', 8081))`, 
# so executing the Python script directly will work perfectly.
CMD ["python", "app.py"]
