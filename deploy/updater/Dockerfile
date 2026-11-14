# The updater (docker-compose.yml, service wa-updater): Docker's own command-line image with compose, plus what
# scripts/auto-deploy.sh and scripts/sync.sh use. It runs the scripts from the project folder, mounted at its own path.
FROM docker:28-cli
RUN apk add --no-cache bash curl git coreutils util-linux tzdata findutils sed grep python3 \
 && git config --system --add safe.directory '*'
CMD ["bash", "scripts/updater.sh"]
