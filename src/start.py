"""Process launcher.

Configures logging before uvicorn gets a chance to install its own dictConfig
(hence log_config=None), then serves app.main:app.
"""

import logging
import sys

import uvicorn

from app.config import get_settings

LOGFORMAT = "%(asctime)s [%(name)-18s] [%(levelname)-5s] %(message)s"

try:
    settings = get_settings()
except Exception as exc:
    logging.basicConfig(level=logging.INFO, format=LOGFORMAT)
    logging.fatal("Configuration error: %s", exc)
    sys.exit(1)

logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO, format=LOGFORMAT)


class SuppressHealthCheckAccessLog(logging.Filter):
    """Drop successful health-check hits from the access log.

    uvicorn.access logs with args (client_addr, method, full_path, http_version,
    status_code), so this matches on those rather than the formatted string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, path, _, status_code = args
        return not (method == "GET" and path == "/healthz" and isinstance(status_code, int) and status_code < 400)


# These are chatty at DEBUG and rarely tell us anything we want.
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

if not settings.DEBUG:
    logging.getLogger("uvicorn.access").addFilter(SuppressHealthCheckAccessLog())

logging.info("Starting scoutid-keycloak-admin on port %d", settings.PORT)

if settings.FAKE_USER_CLAIMS:
    banner = "!" * 72
    logging.warning(
        "%s\n"
        "!! FAKE_USER_CLAIMS is set — normal login is BYPASSED.\n"
        "!! /auth/login signs in %s without authenticating anyone.\n"
        "!! This must never be set in production.\n"
        "%s",
        banner,
        settings.FAKE_USER_CLAIMS.get("preferred_username") or settings.FAKE_USER_CLAIMS.get("sub"),
        banner,
    )

try:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        log_config=None,
        # Behind Traefik, which terminates TLS.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
except Exception as exc:
    logging.fatal("Fatal error: %s", exc, exc_info=True)

logging.info("Stopping scoutid-keycloak-admin")
