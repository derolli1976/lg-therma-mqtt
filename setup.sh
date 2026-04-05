#!/bin/bash
# Quick Setup Script für Proxmox Container

set -e

echo "=== pyThermaV Data Logger Setup ==="
echo ""

# Prüfe ob .env existiert
if [ ! -f .env ]; then
    echo "❌ .env Datei nicht gefunden!"
    echo "Bitte .env.example kopieren und anpassen:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

# Lade .env
source .env

# Validiere Pflichtfelder
if [ -z "$LG_EMAIL" ] || [ -z "$LG_PASSWORD" ] || [ -z "$DEVICE_ID" ]; then
    echo "❌ LG ThinQ Credentials fehlen in .env!"
    exit 1
fi

if [ -z "$SQL_SERVER" ] || [ -z "$SQL_DATABASE" ] || [ -z "$SQL_USERNAME" ] || [ -z "$SQL_PASSWORD" ]; then
    echo "❌ SQL Server Credentials fehlen in .env!"
    exit 1
fi

echo "✅ Konfiguration validiert"
echo ""

# Prüfe Docker Installation
if ! command -v docker &> /dev/null; then
    echo "📦 Docker wird installiert..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

if ! command -v docker-compose &> /dev/null; then
    echo "📦 Docker Compose wird installiert..."
    apt-get update
    apt-get install -y docker-compose
fi

echo "✅ Docker installiert"
echo ""

# Erstelle logs Verzeichnis
mkdir -p logs
chmod 777 logs

# Baue Container
echo "🔨 Container wird gebaut..."
docker-compose build

# Starte Container
echo "🚀 Container wird gestartet..."
docker-compose up -d

# Warte kurz
sleep 3

# Zeige Status
echo ""
echo "=== Status ==="
docker-compose ps

echo ""
echo "=== Logs (Strg+C zum Beenden) ==="
docker-compose logs -f

