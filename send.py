#!/usr/bin/env python3
import json, os, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import feedparser

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "data" / "state.json"
TZ = ZoneInfo("America/Sao_Paulo")
TG = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}"
CHAT = os.environ["TELEGRAM_CHAT_ID"]

SLOTS = [
    ("07:30", "jp", "botafogo"),
    ("09:00", "de", "botafogo"),
    ("10:30", "jp", "literatura"),
    ("12:00", "de", "literatura"),
    ("13:30", "jp", "politica"),
    ("15:00", "de", "politica"),
    ("16:30", "jp", "bolsa"),
    ("18:00", "de", "bolsa"),
    ("19:30", "jp", "brasilia"),
    ("21:00", "de", "brasilia"),
]

TITULO = {
    ("jp", "botafogo"): "ボタフォゴ",
    ("de", "botafogo"): "Botafogo",
    ("jp", "literatura"): "古典文学",
    ("de", "literatura"): "Klassische Literatur",
    ("jp", "politica"): "ブラジル政治",
    ("de", "politica"): "Brasilianische Politik",
    ("jp", "bolsa"): "米国株式市場",
    ("de", "bolsa"): "US-Aktienmarkt",
    ("jp", "brasilia"): "ブラジリア",
    ("de", "brasilia"): "Brasília",
}

FEEDS = {
    "botafogo": ["https://ge.globo.com/rss/futebol/times/botafogo/"],
    "literatura": ["https://www.theguardian.com/books/rss"],
    "politica": ["https://g1.globo.com/rss/g1/politica/"],
    "bolsa": ["https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,AAPL,NVDA&region=US&lang=en-US"],
    "brasilia": ["https://g1.globo.com/rss/g1/df/"],
}

def load_state():
    if not STATE_PATH.exists():
        return {"last_update_id": 0, "skip_date": None, "nivel_jp": "N3-N2", "nivel_de": "A2-B1", "enviados": [], "palavras": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))

def save_state(st):
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

def slot_agora(st):
    forced = os.environ.get("SLOT_HORA") or ""
    now = datetime.now(TZ)
    if forced.strip():
        for h, lang, tema in SLOTS:
            if h == forced.strip():
                return now, h, lang, tema
        raise SystemExit(f"Slot inválido fornecido: {forced}")
    best = min(
        SLOTS,
        key=lambda s: abs(
            (now.hour * 60 + now.minute)
            - (int(s[0][:2]) * 60 + int(s[0][3:]))
        ),
    )
    return now, best[0], best[1], best[2]

def processar_updates(st):
    try:
        r = requests.get(f"{TG}/getUpdates", params={"offset": st.get("last_update_id", 0) + 1, "timeout": 0}, timeout=15)
        r.raise_for_status()
        for u in r.json().get("result", []):
            st["last_update_id"] = u["update_id"]
            text = (u.get("message") or {}).get("text") or ""
            t = text.strip().lower()
            if "hoje sem bot" in t:
                st["skip_date"] = datetime.now(TZ).date().isoformat()
            if t in ("fácil", "facil"):
                st["nivel_de"] = "A2"
                st["nivel_jp"] = "N3"
            if t in ("difícil", "dificil"):
                st["nivel_de"] = "B1"
                st["nivel_jp"] = "N2"
            if t in ("soube", "não soube", "nao soube"):
                for p in reversed(st.get("palavras", [])):
                    if p.get("aberto"):
                        p["acertos"] = p.get("acertos", 0) + (1 if t == "soube" else 0)
                        p["aberto"] = False
                        if t != "soube":
                            p["proxima"] = datetime.now(TZ).date().isoformat()
                        break
    except Exception as e:
        print(f"Erro ao processar respostas do Telegram: {e}")
    return st

def manchetes(tema):
    watchlist_file = ROOT / "data" / "watchlist.txt"
    tickers = watchlist_file.read_text(encoding="utf-8") if watchlist_file.exists() else ""
    linhas = []
    if tema == "bolsa" and tickers:
        linhas.append("Watchlist: " + ", ".join(tickers.split()))
    
    for url in FEEDS.get(tema, []):
        try:
            d = feedparser.parse(url)
            for e in d.entries[:4]:
                linhas.append(f"- {e.get('title', '')} | {e.get('link', '')}")
        except Exception as err:
            print(f"Erro lendo feed {url}: {err}")
            
    return "\n".join(linhas) or "- Sem feed recente. Escreva um resumo de contexto geral da semana."

def gerar(lang, tema, nivel, fonte, palavra_ontem):
    prompt_file = ROOT / "prompts" / f"{lang}.txt"
    prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
    user = f"Tema={tema}\nNível={nivel}\nFontes:\n{fonte}\nEvite usar como palavra do dia: {palavra_ontem or '-'}"
    
    # Lê a chave do Gemini a partir do Secret do GitHub
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        titulo = TITULO.get((lang, tema), "Notícia")
        return {
            "titulo_tema": titulo,
            "texto": "Chave de API não configurada nos Secrets do GitHub.",
            "palavra": "ニュース" if lang == "jp" else "die Nachricht",
            "leitura": "にゅーす" if lang == "jp" else "",
            "sentido_pt": "notícia",
            "exemplo": "",
        }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    payload = {
        "system_instruction": {
            "parts": [{"text": prompt}]
        },
        "contents": [{
            "parts": [{"text": user}]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "response_mime_type": "application/json"
        }
    }

    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=60)
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.M)
        return json.loads(clean_json)
    except Exception as e:
        print(f"Erro na chamada do Gemini: {e}")
        titulo = TITULO.get((lang, tema), "Notícia")
        return {
            "titulo_tema": titulo,
            "texto": f"Falha ao gerar a notícia com a IA ({e}).",
            "palavra": "エラー" if lang == "jp" else "der Fehler",
            "leitura": "えらー" if lang == "jp" else "",
            "sentido_pt": "erro",
            "exemplo": "",
        }

def palavra_de_ontem(st, lang, tema, hoje):
    for p in st.get("palavras", []):
        if p.get("lang") == lang and p.get("tema") == tema and p.get("data", "") < hoje:
            if p.get("proxima", hoje) <= hoje and p.get("acertos", 0) < 2:
                return p
    return None

def enviar_texto(texto):
    requests.post(f"{TG}/sendMessage", json={
        "chat_id": CHAT,
        "text": texto,
        "disable_web_page_preview": True,
    }, timeout=30).raise_for_status()

def main():
    st = load_state()
    st = processar_updates(st)
    now, hora, lang, tema = slot_agora(st)
    hoje = now.date().isoformat()
    chave = f"{hoje}-{hora}-{lang}-{tema}"

    if st.get("skip_date") == hoje:
        save_state(st)
        return
    if chave in st.get("enviados", []):
        save_state(st)
        return

    ontem = palavra_de_ontem(st, lang, tema, hoje)
    nivel = st.get("nivel_jp", "N3-N2") if lang == "jp" else st.get("nivel_de", "A2-B1")
    item = gerar(lang, tema, nivel, manchetes(tema), (ontem or {}).get("palavra"))

    bandeira = "🇯🇵" if lang == "jp" else "🇩🇪"
    bloco_hoje = (
        f"今日の単語\n{item['palavra']}（{item['leitura']}）— {item['sentido_pt']}"
        if lang == "jp" and item.get("leitura")
        else f"Wort des Tages\n{item['palavra']} — {item['sentido_pt']}"
    )
    bloco_ontem = ""
    if ontem:
        bloco_ontem = (
            f"\n\n昨日の単語\n{ontem['palavra']} — {ontem['sentido_pt']}\n空欄: escreva a palavra de ontem."
            if lang == "jp"
            else f"\n\nWort von gestern\n{ontem['palavra']} — {ontem['sentido_pt']}\nLücke: schreiben Sie das Wort von gestern."
        )
        ontem["aberto"] = True

    msg = (
        f"{bandeira} {hora} · {item.get('titulo_tema') or TITULO.get((lang, tema), '')}\n"
        f"{'レベル ' + nivel if lang == 'jp' else 'Niveau ' + nivel}\n\n"
        f"{item['texto']}\n\n"
        f"{bloco_hoje}"
        f"{bloco_ontem}\n\n"
        f"Responda no chat do bot: soube / não soube / fácil / difícil"
    )
    enviar_texto(msg)

    st.setdefault("enviados", []).append(chave)
    st.setdefault("palavras", []).append({
        "palavra": item["palavra"],
        "leitura": item.get("leitura", ""),
        "sentido_pt": item["sentido_pt"],
        "exemplo": item.get("exemplo", ""),
        "lang": lang,
        "tema": tema,
        "data": hoje,
        "proxima": hoje,
        "acertos": 0,
        "aberto": False,
    })
    st["enviados"] = st["enviados"][-40:]
    st["palavras"] = st["palavras"][-80:]
    save_state(st)

if __name__ == "__main__":
    main()
