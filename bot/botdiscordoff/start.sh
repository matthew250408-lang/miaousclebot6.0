#!/bin/bash
# Start script for production deployment
# Runs both the Discord bot and the web dashboard simultaneously

# Toujours se placer dans le dossier du script (utile en déploiement VM
# où le PWD initial est /home/runner/ et non /home/runner/workspace/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 Starting Miaouscle Bot production environment..."
echo "📁 Working directory: $SCRIPT_DIR"

# Libérer le port 5000 s'il est déjà occupé (workflows dupliqués en dev)
fuser -k 5000/tcp 2>/dev/null || true
sleep 1

# Start the web dashboard with gunicorn (sous-shell pour ne pas changer le PWD parent)
echo "🌐 Starting web dashboard on port 5000..."
( cd "$SCRIPT_DIR/web" && gunicorn --bind=0.0.0.0:5000 --workers=2 app:app ) &
WEB_PID=$!

# Start the Discord bot depuis la racine du projet
echo "🤖 Starting Discord bot..."
python "$SCRIPT_DIR/main.py" &
BOT_PID=$!

echo "✅ Both processes started (web=$WEB_PID, bot=$BOT_PID)"

# Wait for either process to exit
wait -n
echo "⚠️ A process exited. Shutting down..."
kill $WEB_PID $BOT_PID 2>/dev/null
wait
