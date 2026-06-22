#!/bin/sh
set -eu

cat > /usr/share/nginx/html/config.js <<EOF
window.__APP_CONFIG__ = {
  KEYCLOAK_URL: "${VITE_KEYCLOAK_URL:-http://localhost:8090}",
  KEYCLOAK_REALM: "${VITE_KEYCLOAK_REALM:-ai-testcase}",
  KEYCLOAK_CLIENT_ID: "${VITE_KEYCLOAK_CLIENT_ID:-frontend}",
  KEYCLOAK_BACKEND_CLIENT_ID: "${VITE_KEYCLOAK_BACKEND_CLIENT_ID:-backend}",
  KEYCLOAK_PKCE_METHOD: "${VITE_KEYCLOAK_PKCE_METHOD:-S256}"
};
EOF
