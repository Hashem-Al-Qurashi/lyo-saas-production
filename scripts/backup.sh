#!/bin/bash

# PostgreSQL Backup Script
# Runs daily via cron in backup container

BACKUP_DIR="/backups"
DB_NAME="lyo_production"
DB_USER="lyo"
DB_HOST="postgres"
RETENTION_DAYS=7

# Create backup directory if not exists
mkdir -p ${BACKUP_DIR}

# Generate backup filename with timestamp
BACKUP_FILE="${BACKUP_DIR}/lyo_backup_$(date +%Y%m%d_%H%M%S).sql"

# Perform backup
echo "Starting database backup at $(date)"
PGPASSWORD=${POSTGRES_PASSWORD} pg_dump \
    -h ${DB_HOST} \
    -U ${DB_USER} \
    -d ${DB_NAME} \
    --no-password \
    --verbose \
    > ${BACKUP_FILE}

# Compress backup
gzip ${BACKUP_FILE}

echo "Backup completed: ${BACKUP_FILE}.gz"

# Remove old backups
echo "Removing backups older than ${RETENTION_DAYS} days"
find ${BACKUP_DIR} -name "lyo_backup_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

# List current backups
echo "Current backups:"
ls -lh ${BACKUP_DIR}/*.sql.gz 2>/dev/null | tail -5

echo "Backup process completed at $(date)"