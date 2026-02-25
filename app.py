import os
import time
import threading
import base64
import requests
from flask import Flask, request, abort

app = Flask(__name__)

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LIVECHAT_WEBHOOK_KEY = os.environ.get("LIVECHAT_WEBHOOK_KEY", "")

# LiveChat polling (Agent Chat API)
LIVECHAT_ACCOUNT_ID = os.environ.get("LIVECHAT_ACCOUNT_ID", "")
LIVECHAT_PAT = os.environ.get("LIVECHAT_PAT", "")
LIVECHAT_POLL_SECONDS = int(os.environ.get("LIVECHAT_POLL_SECONDS", "10"))
LIVECHAT_GROUP_IDS = os.environ.get("LIVECHAT_GROUP_IDS", "").strip()  # e.g. "0,1,2"

LIST_CHATS_URL = "https://api.livechatinc.com/v3.5/agent/action/list_chats"

_seen_chat_ids = set()
_last_msg_id_by_chat = {}  # chat_id -> last message event id
_poll_started = False


def tg_send(text: str) -> None:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )
    r.raise_for_status()


def _basic_auth_header(account_id: str, pat: str) -> str:
    raw = f"{account_id}:{pat}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def list_chats():
    if not (LIVECHAT_ACCOUNT_ID and LIVECHAT_PAT):
        return []

    headers = {
        "Content-Type": "application/json",
        "Authorization": _basic_auth_header(LIVECHAT_ACCOUNT_ID, LIVECHAT_PAT),
    }

    payload = {"filters": {"include_active": True}}

    if LIVECHAT_GROUP_IDS:
        try:
            gids = [int(x.strip()) for x in LIVECHAT_GROUP_IDS.split(",") if x.strip()]
            payload["filters"]["group_ids"] = gids
        except Exception:
            pass

    r = requests.post(LIST_CHATS_URL, headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("chats_summary", [])


def extract_last_message(chat_summary: dict):
    # docs sample: last_event_per_type.message.event.id / text
    let = (chat_summary.get("last_event_per_type") or {}).get("message") or {}
    ev = let.get("event") or {}
    msg_id = ev.get("id")
    msg_text = ev.get("text")
    return msg_id, msg_text


def has_assigned_agent(chat_summary: dict) -> bool:
    # Heuristic: if users contains an agent user object
    users = chat_summary.get("users") or []
    for u in users:
        # user object shape differs by version; check common fields
        if u.get("type") == "agent" or u.get("kind") == "agent" or u.get("role") == "agent":
            return True
    return False


def poll_loop():
    global _poll_started
    while True:
        try:
            chats = list_chats()

            for ch in chats:
                chat_id = ch.get("id")
                if not chat_id:
                    continue

                msg_id, msg_text = extract_last_message(ch)

                # 1) New chat discovered
                if chat_id not in _seen_chat_ids:
                    _seen_chat_ids.add(chat_id)

                    # We want queue-stage alerts too; typically queue chats may have no agent assigned yet.
                    # If you only want "unassigned" alerts, uncomment:
                    # if has_assigned_agent(ch): continue

                    preview = ""
                    if isinstance(msg_text, str) and msg_text.strip():
                        preview = msg_text.strip()
                        if len(preview) > 120:
                            preview = preview[:120] + "..."

                    tg_send(
                        "🟡 LiveChat 대기열 새 문의 감지\n"
                        f"- chat_id: {chat_id}\n"
                        + (f"- preview: {preview}\n" if preview else "")
                        "👉 답변: https://my.livechatinc.com/"
                    )

                # 2) New message in existing chat (message id changed)
                if msg_id and _last_msg_id_by_chat.get(chat_id) != msg_id:
                    _last_msg_id_by_chat[chat_id] = msg_id

                    preview = ""
                    if isinstance(msg_text, str) and msg_text.strip():
                        preview = msg_text.strip()
                        if len(preview) > 120:
                            preview = preview[:120] + "..."

                    tg_send(
                        "📩 LiveChat 새 메시지 감지\n"
                        f"- chat_id: {chat_id}\n"
                        + (f"- preview: {preview}\n" if preview else "")
                        "👉 답변: https://my.livechatinc.com/"
                    )

        except Exception as e:
            # 너무 길게 보내면 스팸될 수 있으니 간단히
            tg_send(f"⚠️ LiveChat polling 오류: {type(e).__name__}")

        time.sleep(max(5, LIVECHAT_POLL_SECONDS))


@app.before_request
def start_poller_once():
    global _poll_started
    if _poll_started:
        return
    # 시작 조건: LiveChat 크리덴셜이 있을 때만
    if LIVECHAT_ACCOUNT_ID and LIVECHAT_PAT:
        _poll_started = True
        t = threading.Thread(target=poll_loop, daemon=True)
        t.start()


@app.get("/")
def health():
    return {"ok": True, "service": "livechat-telegram-alert", "polling": bool(LIVECHAT_ACCOUNT_ID and LIVECHAT_PAT)}


@app.post("/livechat/webhook")
def livechat_webhook():
    if request.args.get("key") != LIVECHAT_WEBHOOK_KEY:
        abort(401)

    payload = request.get_json(silent=True) or {}
    event_type = payload.get("action") or payload.get("event") or payload.get("type") or "unknown"
    chat_id = payload.get("chat_id") or payload.get("payload", {}).get("chat_id") or payload.get("data", {}).get("chat_id")

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
        "📩 LiveChat 웹훅 이벤트\n"
        f"- type: {event_type}\n"
        f"- chat_id: {chat_id}\n"
        + (f"- preview: {preview}\n" if preview else "")
        + "👉 답변: https://my.livechatinc.com/"
    )
    tg_send(msg)
    return {"ok": True}
