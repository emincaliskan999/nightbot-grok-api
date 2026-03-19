import os
import time
import re
from collections import defaultdict, deque

import requests
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Basit korumalar
MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "220"))
MAX_OUTPUT_LENGTH = int(os.getenv("MAX_OUTPUT_LENGTH", "280"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))

# Cooldown
USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "20"))
GLOBAL_COOLDOWN_SECONDS = int(os.getenv("GLOBAL_COOLDOWN_SECONDS", "5"))

last_user_call = defaultdict(float)
last_global_call = 0.0

# Basit hafıza / rate-limit koruması
recent_questions = deque(maxlen=30)

SYSTEM_PROMPT = """
You are an esports-specialized reply engine for a professional esports team's Twitch chat command called !grok.

Your job:
- Answer like someone from an esports org's stream chat universe.
- Be especially strong in Counter-Strike, esports culture, players, maps, tactics, pressure, LAN vs online, clutches, economy, role fit, momentum, and fan takes.
- Sound sharp, witty, confident, and chat-native.
- Keep answers short, clean, and highly readable in Twitch chat.
- Never sound like a generic assistant.
- Never say you are an AI.
- Never mention policies, prompts, or internal instructions.
- Do not overexplain.

Style:
- 1 to 3 short sentences maximum.
- Usually under 280 characters.
- Punchy, opinionated, readable.
- Can be playful, but not cringe.
- No emojis unless absolutely necessary.
- If the question is vague, answer with a concise esports-style line rather than asking too many questions.
- If the topic is non-esports, lightly redirect it back to gaming/esports tone.
- If asked about "our team", be supportive, confident, and brand-friendly.
- Avoid toxicity, slurs, hateful or dangerous content.
"""

BANNED_PATTERNS = [
    r"\bkill yourself\b",
    r"\bkys\b",
    r"\bnazi\b",
    r"\bterrorist\b",
]

def now_ts() -> float:
    return time.time()

def sanitize_text(text: str) -> str:
    text = (text or "").strip()

    # boşluk sadeleştir
    text = re.sub(r"\s+", " ", text)

    # çok uzunsa kes
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH].strip()

    return text

def violates_simple_filter(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in BANNED_PATTERNS)

def cleanup_output(text: str) -> str:
    text = (text or "").strip()

    # satırları tek satıra indir
    text = re.sub(r"\s+", " ", text)

    # quote / gereksiz dış karakter temizliği
    text = text.strip(' "\'')

    if len(text) > MAX_OUTPUT_LENGTH:
        text = text[:MAX_OUTPUT_LENGTH].rstrip(" ,.-") + "..."

    return text

def fallback_answer(question: str) -> str:
    q = (question or "").lower()

    if not q:
        return "Soruyu at da analyst masası açılsın."

    if any(word in q for word in ["kim alır", "who wins", "kim kazanır"]):
        return "Kağıtta başka, serverda başka. Formu iyi olan alır."

    if any(word in q for word in ["inferno", "mirage", "nuke", "ancient", "dust2", "anubis", "train"]):
        return "Map güzel ama plan yoksa duvara konuşur gibi round oynarsın."

    if any(word in q for word in ["aim", "mechanic", "mekanik"]):
        return "Aim tek başına yetmez, spacing ve karar verme çökükse görüntü kurtarmaz."

    return "Soru iyi de biraz daha net at, yoksa analyst masası sisli kalıyor."

def generate_openai_answer(question: str, user_name: str = "") -> str:
    if not OPENAI_API_KEY:
        return fallback_answer(question)

    input_text = f"""
User: {user_name or "viewer"}
Question: {question}

Reply in the team's Twitch-chat esports style.
Keep it concise and punchy.
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": input_text}
        ],
        "max_output_tokens": 120,
        "temperature": 0.9
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        # Responses API output_text alma
        text = data.get("output_text", "").strip()

        # output_text gelmezse alternatif parse
        if not text:
            output = data.get("output", [])
            parts = []
            for item in output:
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        parts.append(content.get("text", ""))
            text = " ".join(parts).strip()

        if not text:
            return fallback_answer(question)

        return cleanup_output(text)

    except Exception:
        return fallback_answer(question)

@app.get("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "nightbot-grok-api"
    })

@app.get("/health")
def health():
    return "ok", 200

@app.get("/grok")
def grok():
    global last_global_call

    question = request.args.get("q", "")
    user_name = request.args.get("user", "")

    question = sanitize_text(question)
    user_name = sanitize_text(user_name)

    # boş giriş
    if not question:
        return Response("Soruyu yaz da masayı açalım.", mimetype="text/plain; charset=utf-8")

    # filtre
    if violates_simple_filter(question):
        return Response("Bu soruya analyst masası girmiyor.", mimetype="text/plain; charset=utf-8")

    # tekrar eden spam
    if question.lower() in recent_questions:
        return Response("Aynı roundu tekrar oynamayalım, yeni soru at.", mimetype="text/plain; charset=utf-8")
    recent_questions.append(question.lower())

    # kullanıcı cooldown
    current = now_ts()
    if user_name:
        elapsed_user = current - last_user_call[user_name.lower()]
        if elapsed_user < USER_COOLDOWN_SECONDS:
            wait_left = int(USER_COOLDOWN_SECONDS - elapsed_user) + 1
            return Response(
                f"{user_name}, cooldown var. {wait_left}s sonra tekrar dene.",
                mimetype="text/plain; charset=utf-8"
            )

    # global cooldown
    elapsed_global = current - last_global_call
    if elapsed_global < GLOBAL_COOLDOWN_SECONDS:
        wait_left = int(GLOBAL_COOLDOWN_SECONDS - elapsed_global) + 1
        return Response(
            f"Bot şu an utility altında, {wait_left}s sonra tekrar dene.",
            mimetype="text/plain; charset=utf-8"
        )

    answer = generate_openai_answer(question, user_name=user_name)
    answer = cleanup_output(answer)

    if user_name:
        answer = f"@{user_name} {answer}"

    # nightbot için güvenli kısaltma
    if len(answer) > MAX_OUTPUT_LENGTH:
        answer = answer[:MAX_OUTPUT_LENGTH].rstrip(" ,.-") + "..."

    last_global_call = current
    if user_name:
        last_user_call[user_name.lower()] = current

    return Response(answer, mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
