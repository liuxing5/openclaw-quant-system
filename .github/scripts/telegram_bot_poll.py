"""Telegram bot polling for overnight_8step.

调用方式：每 5 分钟一次 cron 触发，BJT 9-15 才真正干活。
状态管理：不存任何外部状态。Telegram 的 getUpdates(offset=N) 协议会让
服务器自动删除 update_id < N 的消息，所以只要本次结束前调一次
getUpdates(offset=last_id+1) 做 ACK，下次再 getUpdates() 就只会拿到新消息。
"""
import os
import sys
import requests

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

if not BOT_TOKEN:
    print('未配置 TELEGRAM_BOT_TOKEN，跳过')
    sys.exit(0)

# position_manager 在 strategies/overnight_8step 下，调用方需 cd 过来
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../../strategies/overnight_8step')
from position_manager import handle_command, format_positions  # noqa: E402

API = f'https://api.telegram.org/bot{BOT_TOKEN}'


def get_updates(offset=None, timeout=0):
    params = {'limit': 50, 'timeout': timeout}
    if offset is not None:
        params['offset'] = offset
    resp = requests.get(f'{API}/getUpdates', params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get('ok'):
        raise RuntimeError(f'Telegram API: {data}')
    return data.get('result', [])


def send(chat_id, text):
    requests.post(f'{API}/sendMessage', json={'chat_id': chat_id, 'text': text}, timeout=10)


def dispatch(command, args):
    cmd = command.lower()
    if cmd in ('positions', 'list'):
        return format_positions()
    if cmd == 'add':
        return handle_command('add', args)
    if cmd in ('remove', 'del', 'delete'):
        return handle_command('remove', args)
    if cmd == 'help':
        return handle_command('help')
    if cmd == 'start':
        return ('👋 OpenClaw 量化交易系统\n\n'
                '/positions 查看持仓\n'
                '/add <代码> <成本> [路径] 添加持仓\n'
                '/remove <代码> 删除持仓\n'
                '/help 帮助')
    return f'未知命令: /{command}\n输入 /help 查看可用命令'


def main():
    updates = get_updates()
    if not updates:
        print('无新消息')
        return

    print(f'收到 {len(updates)} 条更新')
    last_id = 0
    for update in updates:
        update_id = update.get('update_id', 0)
        last_id = max(last_id, update_id)

        message = update.get('message') or {}
        chat_id = str(message.get('chat', {}).get('id', ''))
        text = message.get('text', '') or ''

        if CHAT_ID and chat_id != CHAT_ID:
            print(f'忽略未授权 chat: {chat_id}')
            continue
        if not text.startswith('/'):
            continue

        parts = text[1:].split()
        command = parts[0]
        args = ' '.join(parts[1:]) if len(parts) > 1 else ''

        print(f'执行 /{command} {args}')
        try:
            reply = dispatch(command, args)
        except Exception as e:
            reply = f'⚠️ 命令执行异常: {e}'
        send(chat_id, reply)

    # ACK：下次 getUpdates 不再返回这批消息（不依赖外部状态文件）
    if last_id:
        try:
            get_updates(offset=last_id + 1)
            print(f'已 ACK update_id <= {last_id}')
        except Exception as e:
            print(f'ACK 失败（下次会重复处理这些消息，但命令幂等）: {e}')


if __name__ == '__main__':
    main()
