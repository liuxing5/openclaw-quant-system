import urllib.request
import json
import os
import sys

def send_telegram(title, run_time, output_file, bot_token, chat_id):
    """Send stock pick results to Telegram"""
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            output = f.read(3000)
    except Exception:
        output = 'No output available'

    message = title + '\n\n' + '运行时间: ' + run_time + '\n\n' + output
    message = message[:4000]

    payload = {
        'chat_id': chat_id,
        'text': message
    }

    data = json.dumps(payload).encode('utf-8')
    url = 'https://api.telegram.org/bot' + bot_token + '/sendMessage'
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

    try:
        resp = urllib.request.urlopen(req)
        print('Telegram message sent successfully')
    except Exception as e:
        print('Failed to send: ' + str(e))
        sys.exit(1)

def send_error(title, run_time, bot_token, chat_id):
    """Send error notification to Telegram"""
    message = '选股任务执行失败\n\n时间: ' + run_time + '\n任务: ' + title + '\n\n请检查 GitHub Actions 日志获取详细错误信息。'

    payload = {
        'chat_id': chat_id,
        'text': message
    }

    data = json.dumps(payload).encode('utf-8')
    url = 'https://api.telegram.org/bot' + bot_token + '/sendMessage'
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

    try:
        urllib.request.urlopen(req)
        print('Error notification sent')
    except Exception as e:
        print('Failed to send error: ' + str(e))
        sys.exit(1)

if __name__ == '__main__':
    action = os.environ.get('ACTION', 'send')
    title = os.environ.get('TITLE', '')
    run_time = os.environ.get('RUN_TIME', '')
    bot_token = os.environ.get('BOT_TOKEN', '')
    chat_id = os.environ.get('CHAT_ID', '')
    output_file = os.environ.get('OUTPUT_FILE', '/tmp/output.log')

    if action == 'error':
        send_error(title, run_time, bot_token, chat_id)
    else:
        send_telegram(title, run_time, output_file, bot_token, chat_id)
