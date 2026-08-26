# AgentCore Runtime requires linux/arm64 images. We don't hard-code the
# platform here — `scripts/build-and-push.sh` passes `--platform linux/arm64`
# to `docker buildx build`, which is the modern-buildkit-approved way.
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
