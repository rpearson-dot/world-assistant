"""Build identity, baked into the image at build time (see the Dockerfile)."""

import os

VERSION = os.environ.get("WA_VERSION", "dev")
COMMIT = os.environ.get("WA_COMMIT", "unknown")
