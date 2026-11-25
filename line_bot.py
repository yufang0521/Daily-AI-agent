import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = FastAPI(title="Daily Life Hub LINE Bot")

# 你的專屬金鑰
CHANNEL_ACCESS_TOKEN = os.getenv(
    "LINE_CHANNEL_ACCESS_TOKEN",
    "bCrJKvRJjO4RCeOt+wD6qGwteC0jsN+zei9dQdKzlrNu93cRw1gLuptSUxIS69dtKz8G0Lvf/FwhrbMyF51sdOUhlzt/IpImvSyefIHeGDAdpbzl2VsFfM0s44NfZuqPPbT4wANR+L/j7TIBZpJM5gdB04t89/1O/w1cDnyilFU="
)
CHANNEL_SECRET = os.getenv(
    "LINE_CHANNEL_SECRET",
    "ce002a625072221112079bfa907471b9"
)

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 鎖定台灣時區 (UTC+8)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def init_db():
    conn = sqlite3.connect("family_life.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS baby_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT,
        detail TEXT,
        amount INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS parcels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        code TEXT,
        status TEXT DEFAULT '待取件',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

@app.get("/")
def home():
    return {"status": "ok", "message": "家庭生活小秘書 24H 台灣時區運作中"}

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text.strip()
    reply_text = parse_and_process(user_text)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

def parse_and_process(text: str) -> str:
    # 取得準確的台灣當前時間
    now = datetime.now(TAIPEI_TZ)
    now_str = now.strftime("%H:%M")
    conn = sqlite3.connect("family_life.db")
    c = conn.cursor()

    # 1. 寶寶喝奶 (例: "喝奶 150", "喝 120ml", "喝奶180")
    milk_match = re.search(r'(?:喝奶?|奶)\s*(\d+)\s*(?:ml)?', text, re.I)
    if milk_match:
        amount = int(milk_match.group(1))
        c.execute("INSERT INTO baby_logs (action_type, detail, amount) VALUES (?, ?, ?)",
                  ('喝奶', f'喝奶 {amount}ml', amount))
        conn.commit()
        c.execute("SELECT SUM(amount) FROM baby_logs WHERE action_type='喝奶' AND date(created_at, 'localtime') = date('now', 'localtime')")
        total = c.fetchone()[0] or amount
        conn.close()
        next_hour = (now.hour + 3) % 24
        return f"🍼 已記錄：喝奶 {amount}ml（台灣時間 {now_str}）\n📊 今日累計奶量：{total}ml\n⏰ 預計下次喝奶約在 {next_hour}:{now.minute:02d}（3.5 小時後）"

    # 2. 換尿布 / 大便
    if any(k in text for k in ["大便", "便便", "便", "拉屎"]):
        c.execute("INSERT INTO baby_logs (action_type, detail) VALUES (?, ?)", ('大便', f'便便 ({text})'))
        conn.commit()
        conn.close()
        return f"💩 已記錄大便（台灣時間 {now_str}）\n寶寶腸胃很順暢！尿布已更換乾淨。"

    if any(k in text for k in ["換尿布", "濕尿布", "尿尿", "尿布"]):
        c.execute("INSERT INTO baby_logs (action_type, detail) VALUES (?, ?)", ('換尿布', '更換乾淨尿布'))
        conn.commit()
        conn.close()
        return f"💧 已記錄換尿布（台灣時間 {now_str}）"

    # 3. 包裹信件
    if any(k in text for k in ["包裹", "掛號", "取件碼", "管理室"]):
        c.execute("INSERT INTO parcels (name, code) VALUES (?, ?)", (text, text))
        conn.commit()
        conn.close()
        return f"📦 已登記待取包裹：\n「{text}」\n下班或回家時記得順路領取！"

    # 4. 狀態查詢
    if any(k in text for k in ["狀況", "狀態", "今日", "摘要", "總結"]):
        c.execute("SELECT action_type, detail, created_at FROM baby_logs WHERE date(created_at, 'localtime') = date('now', 'localtime') ORDER BY id DESC LIMIT 3")
        recent = c.fetchall()
        c.execute("SELECT COUNT(*) FROM parcels WHERE status='待取件'")
        parcel_count = c.fetchone()[0]
        conn.close()

        msg = f"🏡【家庭生活今日最新狀態】\n"
        msg += f"🕒 查詢時間：台灣 {now_str}\n"
        msg += f"📦 待取包裹：{parcel_count} 件\n"
        msg += f"🍼 最近作息紀錄：\n"
        if not recent:
            msg += "  目前尚無紀錄\n"
        for r in recent:
            time_part = r[2].split(" ")[1][:5] if " " in str(r[2]) else ""
            msg += f"  • {time_part} {r[1]}\n"
        return msg

    conn.close()
    
    # 5. 一般聊天趣味回覆（溫馨育兒管家口吻）
    if any(k in text for k in ["你好", "嗨", "早安", "午安", "晚安", "哈囉"]):
        return f"👋 你好！我是你們家的生活小秘書～現在是台灣時間 {now_str}。今天有什麼需要幫忙記錄的嗎？"
    if any(k in text for k in ["愛你", "謝謝", "辛苦了", "棒"]):
        return "🥰 不客氣！這是我應該做的，爸爸媽媽辛苦了！寶寶健健康康長大最重要～❤️"
        
    return f"💡 收到訊息：「{text}」\n\n📌 常用指令提示：\n• 輸入「喝奶 150ml」記錄餵奶\n• 輸入「大便了」或「換尿布」記錄生理作息\n• 輸入「包裹 管理室簽收 #102」記錄快遞\n• 輸入「今日狀態」查看全家摘要"
