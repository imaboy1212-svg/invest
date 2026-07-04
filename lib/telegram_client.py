"""Telegram Bot API로 @investwellth 채널에 요약 메시지 + 브리핑 파일 전송."""

import os
from pathlib import Path

import requests

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _token_and_chat() -> tuple[str, str]:
    return os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]


def send_message(text: str) -> None:
    token, chat_id = _token_and_chat()
    url = _API_BASE.format(token=token, method="sendMessage")
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    resp.raise_for_status()


def send_document(file_path: Path, caption: str | None = None) -> None:
    token, chat_id = _token_and_chat()
    url = _API_BASE.format(token=token, method="sendDocument")
    with open(file_path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        resp = requests.post(url, data=data, files={"document": f}, timeout=30)
    resp.raise_for_status()
