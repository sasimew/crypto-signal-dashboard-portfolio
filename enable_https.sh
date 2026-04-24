#!/usr/bin/env sh
set -eu

if [ "${1:-}" = "" ]; then
  echo "Usage: ./enable_https.sh you@example.com"
  exit 1
fi

EMAIL="$1"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROD_CONF="$PROJECT_DIR/nginx.conf"
BOOTSTRAP_CONF="$PROJECT_DIR/nginx.bootstrap.conf"
BACKUP_CONF="$PROJECT_DIR/nginx.conf.prod.bak"

cd "$PROJECT_DIR"

mkdir -p data/certbot/conf data/certbot/www

cp "$PROD_CONF" "$BACKUP_CONF"
restore_prod_conf() {
  if [ -f "$BACKUP_CONF" ]; then
    cp "$BACKUP_CONF" "$PROD_CONF"
    rm -f "$BACKUP_CONF"
  fi
}
trap restore_prod_conf EXIT

cp "$BOOTSTRAP_CONF" "$PROD_CONF"

docker compose up -d nginx

docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --email "$EMAIL" \
  --non-interactive \
  --agree-tos \
  --no-eff-email \
  --keep-until-expiring \
  -d sasi.asia \
  -d www.sasi.asia

cp "$BACKUP_CONF" "$PROD_CONF"
rm -f "$BACKUP_CONF"
trap - EXIT

docker compose up -d --force-recreate nginx

echo "HTTPS enabled for https://sasi.asia"
