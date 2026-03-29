#!/bin/sh
# Replace the default domain with SITE_DOMAIN at container startup.
# This allows the same image to serve dev and prod with different cross-links.
set -e

SITE_DOMAIN="${SITE_DOMAIN:-openktree.com}"

if [ "$SITE_DOMAIN" != "openktree.com" ]; then
  echo "Replacing domain: openktree.com -> $SITE_DOMAIN"
  find /usr/share/nginx/html -type f \( -name '*.html' -o -name '*.js' \) \
    -exec sed -i "s|openktree\.com|${SITE_DOMAIN}|g" {} +
fi

exec "$@"
