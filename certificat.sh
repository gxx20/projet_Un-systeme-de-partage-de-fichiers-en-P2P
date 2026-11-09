#!/bin/sh
# Génère le certificat du central pour une adresse IPv4 et localhost.
set -eu
ip=${1:-127.0.0.1}
dossier=${2:-certs}
umask 077
mkdir -p "$dossier"
if [ -e "$dossier/central.key" ] || [ -e "$dossier/central.crt" ]; then
    echo "Un certificat ou une clé existe déjà dans $dossier" >&2
    exit 1
fi
config=$(mktemp)
trap 'rm -f "$config"' EXIT
cat > "$config" <<EOF
[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=central-p2p
[ext]
subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$ip
basicConstraints=critical,CA:FALSE
extendedKeyUsage=serverAuth
EOF
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
    -config "$config" -keyout "$dossier/central.key" -out "$dossier/central.crt"
