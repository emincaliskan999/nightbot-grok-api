import os
import re
import time
from collections import defaultdict
from typing import Optional

import requests
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "220"))
MAX_OUTPUT_LENGTH = int(os.getenv("MAX_OUTPUT_LENGTH", "280"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))

USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "8"))
GLOBAL_COOLDOWN_SECONDS = int(os.getenv("GLOBAL_COOLDOWN_SECONDS", "2"))

last_user_call = defaultdict(float)
last_global_call = 0.0

BANNED_PATTERNS = [
    r"\bkill yourself\b",
    r"\bkys\b",
    r"\bnazi\b",
]

CASUAL_EXACT = {
    "selam",
    "slm",
    "sa",
    "sea",
    "merhaba",
    "hello",
    "hi",
    "hey",
    "hg",
    "ho",
    "naber",
    "napıyon",
    "napiyon",
    "napiyon",
    "nasılsın",
    "nasilsin",
    "iyi misin",
    "iyi yayınlar",
    "iyi yayinlar",
    "kolay gelsin",
    "noluyo",
    "noluyor",
}

CASUAL_KEYWORDS = [
    "selam",
    "merhaba",
    "hello",
    "hi",
    "hey",
    "naber",
    "nasılsın",
    "nasilsin",
    "iyi misin",
    "iyi yayınlar",
    "iyi yayinlar",
    "kolay gelsin",
    "yayın nasıl",
    "yayin nasil",
    "hoş geldik",
    "hos geldik",
]

ESPORTS_KEYWORDS = [
    "cs",
    "cs2",
    "counter",
    "counter-strike",
    "sangal",
    "maç",
    "mac",
    "match",
    "oyuncu",
    "player",
    "takım",
    "team",
    "igl",
    "entry",
    "awp",
    "lurker",
    "anchor",
    "aim",
    "macro",
    "mental",
    "tempo",
    "utility",
    "spacing",
    "trade",
    "clutch",
    "eco",
    "force",
    "anti-eco",
    "execute",
    "default",
    "retake",
    "ct",
    "t-side",
    "ct-side",
    "mirage",
    "dust2",
    "inferno",
    "nuke",
    "ancient",
    "anubis",
    "train",
    "vertigo",
    "overpass",
    "kazanır",
    "kazanir",
    "kim alır",
    "kim alir",
    "kim kazanır",
    "kim kazanir",
    "win",
    "lose",
    "washed",
    "fraud",
    "meta",
    "tier 1",
    "tier1",
    "tier 2",
    "tier2",
    "lan",
    "online",
]

MEME_KEYWORDS = [
    "noob",
    "çöp",
    "cop",
    "satıldı",
    "satildi",
    "boostla",
    "ben niye kötüyüm",
    "ben niye kotuyum",
    "ben niye noobum",
    "skill issue",
    "fraud check",
    "washed mı",
    "washed mi",
    "roast",
]

CASUAL_PROMPT = """
Sen bir espor takımının Twitch chat botusun.
Kullanıcı günlük bir şey yazdı. Buna doğal, kısa, samimi ve chat uyumlu cevap ver.

Kurallar:
- Türkçe yaz.
- 1 kısa cümle, en fazla 2 kısa cümle.
- Gamer/chat havası olsun.
- Zorlama espor analizi yapma.
- Generic AI gibi konuşma.
- Samimi ama fazla yapay olma.
"""

ESPORTS_PROMPT = """
Sen bir espor takımının Twitch chat botusun.
Kullanıcı espor veya Counter-Strike ile ilgili bir şey sordu.

Kurallar:
- Türkçe yaz.
- Kısa, net, opinionated cevap ver.
- Counter-Strike, haritalar, roller, macro, aim, mental, tempo, utility gibi konulara hakim ol.
- 1 veya 2 kısa cümle yaz.
- Uzun analiz yapma.
- Generic AI gibi konuşma.
- Twitch chatte akıcı okunmalı.
- Takım sorularında destekleyici ama abartısız ol.
"""

MEME_PROMPT = """
Sen bir espor takımının Twitch chat botusun.
Kullanıcı troll, yarı saçma veya meme tarzı bir şey yazdı.

Kurallar:
- Türkçe yaz.
- Kısa, komik, hafif taşlayıcı cevap ver.
- Aşağılayıcı veya aşırı toksik olma.
- 1 kısa cümle, gerekirse 2 kısa cümle.
- Cringe olma.
- Twitch chatte hızlı okunacak kadar kısa kal.
"""

def now_ts() -> float:
    return time.time()

def sanitize_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH].strip()
    return text

def violates_simple_filter(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in BANNED_PATTERNS)

def cleanup_output(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(' "\'')

    if len(text) > MAX_OUTPUT_LENGTH:
        text = text[:MAX_OUTPUT_LENGTH].rstrip(" ,.-") + "..."

    return text

def contains_any(text: str, words) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)

def classify_message(question: str) -> str:
    q = question.lower().strip()

    if q in CASUAL_EXACT:
        return "casual"

    if contains_any(q, ESPORTS_KEYWORDS):
        return "esports"

    if contains_any(q, MEME_KEYWORDS):
        return "meme"

    if contains_any(q, CASUAL_KEYWORDS):
        return "casual"

    if len(q) <= 12:
        return "casual"

    return "general"

def rule_based_answer(question: str, mode: str) -> Optional[str]:
    q = question.lower().strip()

    if mode == "casual":
        if q in {"selam", "slm", "sa", "sea", "merhaba", "hello", "hi", "hey"}:
            return "Selam, chat ayakta."
        if q in {"naber", "napıyon", "napiyon", "napiyon"}:
            return "İyiyiz, lobby sıcak."
        if q in {"nasılsın", "nasilsin", "iyi misin"}:
            return "Ayaktayız, server açık."
        if "iyi yayınlar" in q or "iyi yayinlar" in q:
            return "Eyvallah, sen de hoş geldin."
        if "kolay gelsin" in q:
            return "Eyvallah, utility bol olsun."

    if mode == "esports":
        if "sangal" in q and any(x in q for x in ["kazan", "alır", "alir", "win", "bugün", "bugun"]):
            return "Temiz başlangıç gelirse alırız. Tempo bizdeyse maç da bize döner."
        if "mirage" in q and "dust2" in q:
            return "Mirage daha tam paket, Dust2 daha saf kavga."
        if "inferno" in q and "mirage" in q:
            return "Inferno plan ister, Mirage alan verir. Takımın yapısına bakar."
        if "aim" in q and "macro" in q:
            return "Aim round açar, macro maç alır."
        if any(x in q for x in ["kim alır", "kim alir", "kim kazanır", "kim kazanir", "who wins"]):
            return "Kağıt başka, server başka. Formu ve mentalı sağlam olan alır."
        if "washed" in q:
            return "Washed damgası kolay vurulur. Bazen oyuncu değil sistem düşer."

    if mode == "meme":
        if "noob" in q:
            return "Aim yetmemiş, özgüven fazla gelmiş."
        if "satıldı" in q or "satildi" in q:
            return "Satıldı demek kolay, kötü oynandı demek daha dürüst."
        if "skill issue" in q:
            return "Bunda rapor kısa: evet biraz öyle."

    if len(q) <= 3:
        return "Bu kadar kısa atarsan ben de eco cevap dönerim."

    return None

def fallback_answer(question: str, mode: str) -> str:
    q = (question or "").lower()

    if mode == "casual":
        return "Buradayız, chate bağlandık."

    if mode == "esports":
        if any(word in q for word in ["map", "mirage", "dust2", "inferno", "nuke", "ancient", "anubis", "train"]):
            return "Map tek başına kurtarmaz, setup kötüyse güzel harita da çöker."
        if any(word in q for word in ["takım", "team", "oyuncu", "player"]):
            return "İsimden çok uyum oynar. Kağıttaki beşliyle serverdaki beşli aynı olmuyor."
        return "Bu soru oynar ama biraz daha açarsan daha temiz vururum."

    if mode == "meme":
        return "Burada biraz skill issue kokusu aldım."

    return "Mesaj geldi, ben hazırım."

def generate_openai_answer(question: str, mode: str, user_name: str = "") -> str:
    if not OPENAI_API_KEY:
        return fallback_answer(question, mode)

    if mode == "casual":
        system_prompt = CASUAL_PROMPT
    elif mode == "esports":
        system_prompt = ESPORTS_PROMPT
    elif mode == "meme":
        system_prompt = MEME_PROMPT
    else:
        system_prompt = CASUAL_PROMPT

    user_input = f"""
Kullanıcı adı: {user_name or "viewer"}
Mesaj: {question}

Mod: {mode}
Kısa, doğal, Türkçe ve Twitch chat uyumlu cevap ver.
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "max_output_tokens": 90,
        "temperature": 1.0,
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

        text = data.get("output_text", "").strip()

        if not text:
            output = data.get("output", [])
            parts = []
            for item in output:
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        parts.append(content.get("text", ""))
            text = " ".join(parts).strip()

        if not text:
            return fallback_answer(question, mode)

        return cleanup_output(text)

    except Exception:
        return fallback_answer(question, mode)

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

    if not question:
        return Response("Soruyu sal da masa kurulsun.", mimetype="text/plain; charset=utf-8")

    if violates_simple_filter(question):
        return Response("Bu soruya girmiyorum.", mimetype="text/plain; charset=utf-8")

    current = now_ts()

    if user_name:
        elapsed_user = current - last_user_call[user_name.lower()]
        if elapsed_user < USER_COOLDOWN_SECONDS:
            wait_left = int(USER_COOLDOWN_SECONDS - elapsed_user) + 1
            return Response(
                f"{wait_left}s cooldown.",
                mimetype="text/plain; charset=utf-8"
            )

    elapsed_global = current - last_global_call
    if elapsed_global < GLOBAL_COOLDOWN_SECONDS:
        wait_left = int(GLOBAL_COOLDOWN_SECONDS - elapsed_global) + 1
        return Response(
            f"Bot meşgul, {wait_left}s sonra tekrar.",
            mimetype="text/plain; charset=utf-8"
        )

    mode = classify_message(question)

    answer = rule_based_answer(question, mode)
    if not answer:
        answer = generate_openai_answer(question, mode, user_name=user_name)

    answer = cleanup_output(answer)

    last_global_call = current
    if user_name:
        last_user_call[user_name.lower()] = current

    return Response(answer, mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
