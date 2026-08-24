#!/usr/bin/env bash
# SSL cert renewal check/fetch for news.wfc.welfarecall.com
# Adapted from the standard WFC cert-fetch pattern (linuxman01 cert inventory).
# Credentials are NOT stored in this file — see /etc/cyber-ai-news/cert-fetch.env
# (root-only, chmod 600), sourced below.

set -euo pipefail

ENV_FILE=/etc/cyber-ai-news/cert-fetch.env
if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE — cannot fetch cert credentials." >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$ENV_FILE"
# Expects CERT_FETCH_USER and CERT_FETCH_PASSWORD to be set by the env file.

certfile=/etc/ssl/certs/cert.pem

# Check if certificate expires within ~14 days (also true if no cert exists yet)
if [ -f "$certfile" ] && openssl x509 -checkend 1210000 -noout -in "$certfile" > /dev/null
then
    # Certificate is still valid
    exit 0
fi

logfile=$(mktemp)

# Get end date (empty on first run, when no cert exists yet)
enddate=$(openssl x509 -enddate -noout -in "$certfile" 2>/dev/null | awk -F= '{print $2}' || echo "none")

sslhost="https://linuxman01.wfc.welfarecall.com/certs/news.wfc.welfarecall.com"
mailto="itsupport@welfarecall.com,msaunders@welfarecall.com"

echo "Certificate on $(hostname) due to expire: $enddate — attempting update from $sslhost" > "$logfile"

chmod 0711 /etc/ssl/private

for file in cert.pem chain.pem fullchain.pem; do
    echo -n "Grabbing $file: " >> "$logfile"
    curl -skf --user "$CERT_FETCH_USER:$CERT_FETCH_PASSWORD" "$sslhost/$file" \
        -o "/etc/ssl/certs/$file" && echo "OKAY" >> "$logfile" || echo "FAIL" >> "$logfile"
done

echo -n "Grabbing privkey.pem: " >> "$logfile"
curl -skf --user "$CERT_FETCH_USER:$CERT_FETCH_PASSWORD" "$sslhost/privkey.pem" \
    -o /etc/ssl/private/privkey.pem && echo "OKAY" >> "$logfile" || echo "FAIL" >> "$logfile"

new_enddate=$(openssl x509 -enddate -noout -in "$certfile" 2>/dev/null | awk -F= '{print $2}' || echo "none")
echo "New end date: $new_enddate" >> "$logfile"

if [ "$new_enddate" != "$enddate" ]; then
    systemctl reload nginx >> "$logfile" 2>&1 && echo "nginx reloaded." >> "$logfile"
else
    echo "Certificate not updated — check linuxman01." >> "$logfile"
    mailto="$mailto,ticket@welfarecall.com"
fi

# mail may not be installed on all hosts; guard to avoid breaking the script
if command -v mail &>/dev/null; then
    mail -a"From:cyber-ai-news@wfc.welfarecall.com" \
         -s "SSL Certificate Update: $(hostname)" $mailto < "$logfile"
fi

rm "$logfile"
