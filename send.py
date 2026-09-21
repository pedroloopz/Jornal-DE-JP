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
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indentPerfeito! Vamos seguir exatamente a partir daí. 

Como você já inseriu a chave da API, o próximo passo é criar e configurar o arquivo **`send.py`** para realizar o envio das mensagens.

Aqui está o código completo do **`send.py`**. Crie o arquivo no mesmo diretório e cole o conteúdo abaixo:

```python
import os
import requests

# Defina a sua chave de API aqui (ou garanta que ela esteja configurada como variável de ambiente)
API_KEY = os.getenv("API_KEY", "SUA_CHAVE_AQUI")

# Endpoint da API (ajuste a URL para a sua API / provedor de mensagens)
URL = "[https://api.exemplo.com/v1/messages](https://api.exemplo.com/v1/messages)"

def enviar_mensagem(destinatario, texto):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "to": destinatario,
        "message": texto
    }

    try:
        response = requests.post(URL, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            print(f"✅ Mensagem enviada com sucesso para {destinatario}!")
            return response.json()
        else:
            print(f"❌ Erro ao enviar ({response.status_code}): {response.text}")
            return None
    except Exception as e:
        print(f"⚠️ Erro de conexão: {e}")
        return None

if __name__ == "__main__":
    # Teste rápido de envio
    numero_teste = "5511999999999"
    mensagem_teste = "Olá! Testando o envio via script send.py."
    
    print("Iniciando envio de teste...")
    enviar_mensagem(numero_teste, mensagem_teste)
