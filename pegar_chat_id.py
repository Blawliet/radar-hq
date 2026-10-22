"""Descobre o seu TELEGRAM_CHAT_ID.

1. Abra o seu bot no Telegram e toque em Iniciar (ou mande qualquer mensagem).
2. Rode:  python pegar_chat_id.py SEU_TOKEN
"""
import sys

import httpx

if len(sys.argv) < 2:
    sys.exit("Uso: python pegar_chat_id.py SEU_TOKEN_DO_BOTFATHER")

r = httpx.get(f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates", timeout=20).json()
if not r.get("ok"):
    sys.exit(f"Token inválido: {r.get('description')}")

chats = {}
for u in r.get("result", []):
    msg = u.get("message") or u.get("my_chat_member") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""

if not chats:
    sys.exit("Nenhuma mensagem encontrada. Mande um 'oi' para o bot e rode de novo.")
for cid, nome in chats.items():
    print(f"TELEGRAM_CHAT_ID = {cid}   ({nome})")
