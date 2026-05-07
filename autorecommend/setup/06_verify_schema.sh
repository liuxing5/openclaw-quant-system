#!/bin/bash
# Step 2.2 - Verify Tables Creation
# Run this after schema.sql is executed

echo "=== Verifying Database Tables ==="

psql -h localhost -U stockrec -d stockrec_db -c "\dt"

echo ""
echo "=== Expected: 7 tables ==="
echo "- feed_sources"
echo "- raw_signals"
echo "- extracted_recommendations"
echo "- daily_candidates"
echo "- push_history"
echo "- performance_tracking"
echo "- source_performance"
echo ""
echo "=== Verification Complete ==="
