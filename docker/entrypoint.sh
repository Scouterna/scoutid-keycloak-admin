#!/bin/sh
# Write the runtime configuration the SPA reads at startup.
#
# Vite inlines import.meta.env at *build* time, so a prebuilt image cannot be
# retargeted with environment variables alone. Instead the app reads
# window.__SCOUTID_CONFIG__ (see src/config.ts) and this script writes it from
# the container environment, so one image can serve any environment.
#
# nginx runs every executable in /docker-entrypoint.d before starting.
set -eu

CONFIG_FILE=/usr/share/nginx/html/config.js

: "${KC_URL:=}"
: "${KC_REALM:=}"
: "${KC_CLIENT_ID:=}"

cat > "$CONFIG_FILE" <<EOF
window.__SCOUTID_CONFIG__ = {
  authority: "${KC_URL}",
  realm: "${KC_REALM}",
  clientId: "${KC_CLIENT_ID}"
};
EOF

echo "scoutid-admin: config.js written (realm='${KC_REALM}' authority='${KC_URL}')"
