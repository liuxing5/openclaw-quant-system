#!/bin/bash
# Step 0.5 - Configure PostgreSQL
# Sets up PostgreSQL database and user for the stock recommender

echo "=== Configuring PostgreSQL ==="

# Start and enable PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database and user
sudo -u postgres psql <<EOF
CREATE USER stockrec WITH PASSWORD 'CHANGE_TO_STRONG_PASSWORD';
CREATE DATABASE stockrec_db OWNER stockrec;
GRANT ALL PRIVILEGES ON DATABASE stockrec_db TO stockrec;
\q
EOF

# Test connection
psql -h localhost -U stockrec -d stockrec_db -c "SELECT version();"

echo "=== PostgreSQL Configuration Complete ==="
echo "Remember to change the default password!"
