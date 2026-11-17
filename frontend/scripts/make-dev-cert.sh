#!/bin/sh
# Creates a self-signed certificate so the dev server can run over https.
# Browsers only allow the live camera on https (or localhost), so phones on
# your Wi-Fi need this. Usage:  npm run make-cert   [optional LAN IP]
set -e
cd "$(dirname "$0")/.."
mkdir -p .certs

LAN_IP="${1:-$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)}"
SAN="DNS:localhost,IP:127.0.0.1"
[ -n "$LAN_IP" ] && SAN="$SAN,IP:$LAN_IP"

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout .certs/dev-key.pem -out .certs/dev-cert.pem \
  -subj "/CN=FaceTrack dev" \
  -addext "subjectAltName=$SAN" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null
chmod 600 .certs/dev-key.pem

echo "Certificate created for: $SAN"
echo "Start the https dev server with:  npm run dev:https"
echo "Your phone will warn once that the certificate is self-signed; accept it to continue."
