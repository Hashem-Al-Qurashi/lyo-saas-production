#!/bin/bash
set -e

KEY="/home/sakr_quraish/.ssh/lyo-saas-key.pem"
HOST="ec2-user@98.89.13.25"
SSH_OPTS="-i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
LOCAL="/home/sakr_quraish/Projects/italian/lyo-bot/management"
REMOTE="/home/ec2-user/lyo-bot/management"

echo "==> Uploading logo..."
scp $SSH_OPTS "$LOCAL/static/lyo-logo.png" "$HOST:$REMOTE/static/lyo-logo.png"

echo "==> Uploading templates..."
scp $SSH_OPTS $LOCAL/templates/*.html "$HOST:$REMOTE/templates/"

echo "==> Uploading app.py (static file mount)..."
scp $SSH_OPTS "$LOCAL/app.py" "$HOST:$REMOTE/app.py"

echo "==> Restarting service..."
ssh $SSH_OPTS "$HOST" "sudo systemctl restart lyo-bot && sleep 2 && sudo systemctl is-active lyo-bot"

echo "==> Done! Check: http://98.89.13.25/manage/login"
