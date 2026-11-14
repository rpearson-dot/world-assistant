# Small, no root, nothing it does not need. World Assistant runs it read-only with /data (its storage) and /tmp.
FROM python:3.13-slim
RUN useradd --uid 10001 --create-home module && mkdir /data && chown 10001:10001 /data
WORKDIR /app
COPY app.py .
USER 10001
EXPOSE 8080
CMD ["python", "app.py"]
