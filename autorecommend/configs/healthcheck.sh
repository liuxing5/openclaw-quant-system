#!/bin/bash
source ~/stock-recommender/.env
ALERT=""

# 检查服务
for svc in stockrec-collector stockrec-extractor stockrec-bot; do
  if ! systemctl is-active --quiet $svc; then
    ALERT+="❗ $svc DOWN\n"
  fi
done

# 检查近1小时入库量
N=$(psql -h localhost -U $POSTGRES_USER -d $POSTGRES_DB -tAc \
  "SELECT COUNT(*) FROM raw_signals WHERE fetch_time > NOW() - INTERVAL '1 hour';")
if [ "$N" -lt 10 ]; then
  ALERT+="❗ 入库异常: 1h只有 $N 条\n"
fi

# 检查 RSSHub
if ! curl -sf "http://localhost:1200/cls/telegraph?key=$RSSHUB_ACCESS_KEY" > /dev/null; then
  ALERT+="❗ RSSHub 不通\n"
fi

# 检查每个源失败次数
FAILED=$(psql -h localhost -U $POSTGRES_USER -d $POSTGRES_DB -tAc \
  "SELECT COUNT(*) FROM feed_sources WHERE consecutive_failures >= 5;")
if [ "$FAILED" -gt 0 ]; then
  ALERT+="❗ $FAILED 个源连续失败\n"
fi

# 推送告警
if [ -n "$ALERT" ]; then
  curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
    -d "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode "text=🚨 系统告警:\n$ALERT"
fi
