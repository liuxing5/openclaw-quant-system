#!/bin/bash
source ~/stock-recommender/.env
BAK=~/stock-recommender/data/backups
mkdir -p $BAK
DATE=$(date +%Y%m%d)
PGPASSWORD=$POSTGRES_PASSWORD pg_dump -h localhost -U $POSTGRES_USER $POSTGRES_DB | gzip > $BAK/db_$DATE.sql.gz
# 保留 30 天
find $BAK -name "db_*.sql.gz" -mtime +30 -delete
