FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY . /app
# Bothost mounts /app and /app/data at runtime; build-time chown is hidden.
# Use the image's default user so the platform's mounted data stays writable.
RUN mkdir -p /app/data
EXPOSE 3000
CMD ["python", "-u", "bot.py"]
