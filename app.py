import os
import requests
from flask import Flask, request, abort

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LIVECHAT_WEBHOOK_KEY = os.environ.get("LIVECHAT_WEBHOOK_KEY", "")

def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    r.raise_for_status()

@app.get("/")
def health():
    return {"ok": True, "service": "livechat-telegram-alert"}

@app.post("/livechat/webhook")
def livechat_webhook():
    if request.args.get("key") != LIVECHAT_WEBHOOK_KEY:
        abort(401)

    payload = request.get_json(silent=True) or {}

    event_type = payload.get("action") or payload.get("event") or payload.get("type") or "unknown"
    chat_id = (
        payload.get("chat_id")
        or payload.get("payload", {}).get("chat_id")
        or payload.get("data", {}).get("chat_id")
    )
    text = (
        payload.get("text")
        or payload.get("payload", {}).get("event", {}).get("text")
        or payload.get("payload", {}).get("text")
        or payload.get("data", {}).get("event", {}).get("text")
    )

    preview = ""
    if isinstance(text, str) and text.strip():
        preview = text.strip()
        if len(preview) > 120:
            preview = preview[:120] + "..."

    msg = (
        "📩 LiveChat 메시지 도착\n"
        f"- type: {event_type}\n"
        f"- chat_id: {chat_id}\n"
        + (f"- preview: {preview}\n" if preview else "")
        + "👉 답변하러 가기: https://my.livechatinc.com/"
    )

    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        # 환경변수 없을 때 로컬에서 테스트해도 서버가 죽지 않게 방어
        return {"ok": True, "warning": "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", "msg": msg}

    tg_send(msg)
    return {"ok": True}
