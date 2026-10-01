
from __future__ import annotations
import requests

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("Telegram bot token/chat id is missing")
        self.bot_token=bot_token
        self.chat_id=str(chat_id)

    def __call__(self, text: str):
        url=f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        r=requests.post(
            url,
            json={
                "chat_id":self.chat_id,
                "text":text,
                "disable_web_page_preview":True,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
