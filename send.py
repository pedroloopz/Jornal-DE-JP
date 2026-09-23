#!/usr/bin/env python3
"""Jornal de idiomas no Telegram — japonês (N3–N2) e alemão (A2–B1).

MODO=jornal (padrão): envia o texto do slot + áudios (normal e shadowing) + quiz.
MODO=respostas: só lê o chat e responde (correções, voz, confirmações, resumo).

Comandos no chat: soube · não soube · fácil · difícil · hoje sem bot · voltar · nivel · resumo
Qualquer outra frase em japonês/alemão = frase para correção. Mensagem de voz = avaliação de pronúncia.
"""
import base64
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

ROOT = Path(__file__).parent
DATA = ROOT / "data"
PROMPTS = ROOT / "prompts"
STATE_PATH = DATA / "state.json"
WATCHLIST_PATH = DATA / "watchlist.txt"
TZ = ZoneInfo("America/Sao_Paulo")
TMP = Path(tempfile.gettempdir())
TEM_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

TOKEN = os.environ["TELEGRAM_TOKEN"]
TG = f"https://api.telegram.org/bot{TOKEN}"
TG_FILE = f"https://api.telegram.org/file/bot{TOKEN}"
CHAT = str(os.environ["TELEGRAM_CHAT_ID"]).strip()
MODO = (os.environ.get("MODO") or "jornal").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODELS = list(dict.fromkeys(m for m in [
    os.environ.get("GEMINI_MODEL", "").strip(),
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
] if m))
UA = {"User-Agent": "Mozilla/5.0 (jornal-idiomas-bot)"}
e = html.escape

# ---------------------------------------------------------------- horários
# (hora BRT, idioma, posição do dia). Para mudar um horário, mude aqui E o cron no jornal.yml.
SLOTS = [
    ("06:30", "jp", 0),
    ("08:00", "de", 0),
    ("12:30", "jp", 1),
    ("17:30", "de", 1),
    ("19:30", "jp", 2),
    ("21:00", "de", 2),
]
CRON_SLOT = {  # cron (UTC) -> slot (BRT = UTC-3)
    "33 9 * * *": "06:30",
    "3 11 * * *": "08:00",
    "33 15 * * *": "12:30",
    "33 20 * * *": "17:30",
    "33 22 * * *": "19:30",
    "3 0 * * *": "21:00",
}
TEMAS = ["botafogo", "literatura", "politica", "bolsa", "brasilia"]  # rodízio diário

NIVEIS = {"jp": ["N3", "N3-N2", "N2"], "de": ["A2", "A2-B1", "B1"]}
NIVEL_PADRAO = {"jp": "N3-N2", "de": "A2-B1"}
INTERVALOS = [1, 3, 7, 21]  # dias até a próxima revisão depois de cada acerto seguido
BANDEIRA = {"jp": "🇯🇵", "de": "🇩🇪"}
IDIOMA = {"jp": "japonês", "de": "alemão"}

TITULO = {
    ("jp", "botafogo"): "ボタフォゴ", ("de", "botafogo"): "Botafogo",
    ("jp", "literatura"): "文学", ("de", "literatura"): "Literatur",
    ("jp", "politica"): "ブラジル政治", ("de", "politica"): "Brasilianische Politik",
    ("jp", "bolsa"): "米国株式市場", ("de", "bolsa"): "US-Aktienmarkt",
    ("jp", "brasilia"): "ブラジリア", ("de", "brasilia"): "Brasília",
}

OBRAS = {  # domínio público; o Gemini reconta com as próprias palavras
    "jp": [
        "芥川龍之介『羅生門』", "芥川龍之介『蜘蛛の糸』", "太宰治『走れメロス』",
        "宮沢賢治『注文の多い料理店』", "夏目漱石『坊っちゃん』", "中島敦『山月記』",
        "新美南吉『ごんぎつね』", "夏目漱石『夢十夜』", "宮沢賢治『銀河鉄道の夜』",
        "芥川龍之介『杜子春』",
    ],
    "de": [
        "Brüder Grimm – Hänsel und Gretel", "Franz Kafka – Die Verwandlung",
        "Brüder Grimm – Die Bremer Stadtmusikanten", "Theodor Storm – Der Schimmelreiter",
        "E.T.A. Hoffmann – Der Sandmann", "Heinrich von Kleist – Michael Kohlhaas",
        "Theodor Fontane – Effi Briest", "Goethe – Der Zauberlehrling",
        "Brüder Grimm – Rumpelstilzchen", "Wilhelm Busch – Max und Moritz",
    ],
}


def gnews(q):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


FEEDS = {  # o primeiro que responder é usado
    "botafogo": ["https://ge.globo.com/rss/futebol/times/botafogo/", gnews("Botafogo futebol")],
    "politica": ["https://g1.globo.com/rss/g1/politica/", gnews("política Brasil")],
    "brasilia": ["https://g1.globo.com/rss/g1/df/", gnews("Distrito Federal Brasília")],
}


# ---------------------------------------------------------------- estado
def estado_padrao():
    return {
        "last_update_id": 0, "skip_date": None,
        "nivel_jp": NIVEL_PADRAO["jp"], "nivel_de": NIVEL_PADRAO["de"],
        "ultimo_lang": None, "revisao_aberta": None,
        "ultima_palavra": {}, "ultima_frase": {}, "obra_idx": {"jp": 0, "de": 0},
        "dominadas": {"jp": 0, "de": 0}, "quizzes": {}, "pendentes": [],
        "pedir_resumo": False, "log": [],
        "enviados": [], "links_usados": [], "palavras": [],
    }


def load_state():
    st = estado_padrao()
    if STATE_PATH.exists():
        st.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    for p in st["palavras"]:  # compatibilidade com versões antigas
        p.setdefault("id", f"{p.get('data')}-{p.get('lang')}-{p.get('palavra')}")
        p.setdefault("acertos", 0)
        p.setdefault("proxima", p.get("data"))
        p.pop("aberto", None)
    return st


def save_state(st):
    limite = (datetime.now(TZ).date() - timedelta(days=60)).isoformat()
    st["palavras"] = [p for p in st["palavras"] if not p.get("dominada")][-300:]
    st["enviados"] = st["enviados"][-60:]
    st["links_usados"] = st["links_usados"][-200:]
    st["log"] = [x for x in st["log"] if x.get("d", "") >= limite][-800:]
    st["quizzes"] = dict(list(st["quizzes"].items())[-30:])
    DATA.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def registrar(st, tipo, lang, dia, ok=None, nota=None):
    st["log"].append({"d": dia.isoformat(), "t": tipo, "lang": lang, "ok": ok, "nota": nota})


# ---------------------------------------------------------------- slot
def minutos(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def escolher_slot():
    now = datetime.now(TZ)
    agora = now.hour * 60 + now.minute
    hora = (os.environ.get("SLOT_HORA") or "").strip() or CRON_SLOT.get((os.environ.get("CRON") or "").strip())
    if not hora:
        def dist(s):
            d = abs(agora - minutos(s[0]))
            return min(d, 1440 - d)
        hora = min(SLOTS, key=dist)[0]
    for h, lang, k in SLOTS:
        if h == hora:
            dia = now.date()
            if minutos(h) - agora > 12 * 60:  # slot da noite rodando depois da meia-noite
                dia -= timedelta(days=1)
            return dia, h, lang, k
    raise SystemExit(f"Slot inválido: {hora!r}. Use um destes: {', '.join(s[0] for s in SLOTS)}")


def tema_do_dia(dia, lang, k):
    return TEMAS[(dia.toordinal() * 3 + k + (0 if lang == "jp" else 2)) % len(TEMAS)]


# ---------------------------------------------------------------- Telegram
def enviar_texto(texto):
    r = requests.post(f"{TG}/sendMessage", json={
        "chat_id": CHAT, "text": texto, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram recusou a mensagem: {r.text[:300]}")


def enviar_audios(lista, lang):
    """lista = [(path, título)]. Vários áudios vão juntos num álbum (1 notificação)."""
    if not lista:
        return
    perf = f"Jornal {BANDEIRA[lang]}"
    try:
        if len(lista) == 1:
            path, titulo = lista[0]
            with open(path, "rb") as f:
                requests.post(f"{TG}/sendAudio", data={"chat_id": CHAT, "title": titulo, "performer": perf},
                              files={"audio": (path.name, f, "audio/mpeg")}, timeout=90).raise_for_status()
            return
        media, files = [], {}
        for i, (path, titulo) in enumerate(lista):
            media.append({"type": "audio", "media": f"attach://a{i}", "title": titulo, "performer": perf})
            files[f"a{i}"] = (path.name, open(path, "rb"), "audio/mpeg")
        try:
            requests.post(f"{TG}/sendMediaGroup", data={"chat_id": CHAT, "media": json.dumps(media)},
                          files=files, timeout=120).raise_for_status()
        finally:
            for _, fh, _ in files.values():
                fh.close()
    except Exception as err:
        print(f"Aviso: áudio não enviado: {err}")


def enviar_quiz(item):
    q, ops, c = item.get("pergunta"), [o for o in item.get("opcoes") or [] if o], item.get("correta")
    if not q or len(ops) < 2 or not isinstance(c, int) or not 0 <= c < len(ops):
        return None
    base = {
        "chat_id": CHAT, "question": ("❓ " + q)[:300],
        "options": [{"text": o[:100]} for o in ops[:4]],
        "type": "quiz", "correct_option_id": c,
        "explanation": (item.get("explicacao_pt") or "")[:200],
    }
    for anonimo in (False, True):  # não-anônimo permite registrar o acerto
        try:
            r = requests.post(f"{TG}/sendPoll", json={**base, "is_anonymous": anonimo}, timeout=30)
            if r.ok:
                return None if anonimo else r.json()["result"]["poll"]["id"]
            print(f"Aviso: quiz recusado (anônimo={anonimo}): {r.text[:200]}")
        except requests.RequestException as err:
            print(f"Aviso: quiz não enviado: {err}")
    return None


# ---------------------------------------------------------------- respostas no chat
COMANDOS = {"hoje sem bot", "pausa", "pausar", "voltar", "retomar", "facil", "dificil",
            "soube", "nao soube", "errei", "nivel", "/nivel", "status", "resumo", "/resumo", "/start"}


def normalizar(t):
    t = unicodedata.normalize("NFD", t.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[!.?]+$", "", t).strip()


def idioma_do_texto(t, padrao):
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", t):
        return "jp"
    if re.search(r"[A-Za-zÄÖÜäöüß]", t):
        return "de"
    return padrao


def ajustar_nivel(st, lang, passo):
    escala = NIVEIS[lang]
    atual = st.get(f"nivel_{lang}", NIVEL_PADRAO[lang])
    i = escala.index(atual) if atual in escala else 1
    st[f"nivel_{lang}"] = escala[max(0, min(len(escala) - 1, i + passo))]
    return st[f"nivel_{lang}"]


def registrar_revisao(st, soube, hoje):
    pid = st.get("revisao_aberta")
    p = next((x for x in st["palavras"] if x["id"] == pid), None)
    if not p:
        return "Nenhuma revisão aberta"
    st["revisao_aberta"] = None
    registrar(st, "rev", p["lang"], hoje, ok=soube)
    if soube:
        p["acertos"] += 1
        if p["acertos"] > len(INTERVALOS):
            p["dominada"] = True
            st["dominadas"][p["lang"]] = st["dominadas"].get(p["lang"], 0) + 1
            return f"🏆 {p['palavra']} dominada"
        dias = INTERVALOS[p["acertos"] - 1]
        p["proxima"] = (hoje + timedelta(days=dias)).isoformat()
        return f"✅ {p['palavra']} → volta em {dias} dia(s)"
    p["acertos"] = 0
    p["proxima"] = (hoje + timedelta(days=1)).isoformat()
    return f"🔁 {p['palavra']} → volta amanhã"


def processar_updates(st):
    """Lê o chat. Retorna confirmações; frases e áudios vão para st['pendentes']."""
    confirmacoes = []
    try:
        r = requests.get(f"{TG}/getUpdates", params={
            "offset": st["last_update_id"] + 1, "timeout": 0,
            "allowed_updates": json.dumps(["message", "poll_answer"]),
        }, timeout=20)
        r.raise_for_status()
        updates = r.json().get("result", [])
    except requests.RequestException as err:
        print(f"Aviso: não consegui ler o Telegram: {err}")
        return confirmacoes

    hoje = datetime.now(TZ).date()
    for u in updates:
        st["last_update_id"] = u["update_id"]

        pa = u.get("poll_answer")
        if pa:
            qz = st["quizzes"].pop(pa.get("poll_id"), None)
            if qz and str((pa.get("user") or {}).get("id")) == CHAT:
                registrar(st, "quiz", qz["lang"], hoje, ok=qz["correta"] in (pa.get("option_ids") or []))
            continue

        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT:
            continue
        dia_msg = datetime.fromtimestamp(msg.get("date", time.time()), TZ).date()
        lang = st.get("ultimo_lang") or "jp"

        voz = msg.get("voice") or msg.get("audio")
        if voz:
            st["pendentes"].append({"tipo": "voz", "file_id": voz["file_id"], "lang": lang,
                                    "alvo": st["ultima_frase"].get(lang)})
            continue

        bruto = (msg.get("text") or "").strip()
        t = normalizar(bruto)
        if not bruto:
            continue
        if t in ("hoje sem bot", "pausa", "pausar"):
            st["skip_date"] = dia_msg.isoformat()
            confirmacoes.append(f"⏸️ Pausado em {dia_msg:%d/%m}")
        elif t in ("voltar", "retomar"):
            st["skip_date"] = None
            confirmacoes.append("▶️ Bot retomado")
        elif t == "facil":
            confirmacoes.append(f"⬆️ {IDIOMA[lang].capitalize()}: {ajustar_nivel(st, lang, +1)}")
        elif t == "dificil":
            confirmacoes.append(f"⬇️ {IDIOMA[lang].capitalize()}: {ajustar_nivel(st, lang, -1)}")
        elif t == "soube":
            confirmacoes.append(registrar_revisao(st, True, hoje))
        elif t in ("nao soube", "errei"):
            confirmacoes.append(registrar_revisao(st, False, hoje))
        elif t in ("nivel", "/nivel", "status"):
            confirmacoes.append(f"📊 Japonês {st['nivel_jp']} · Alemão {st['nivel_de']}")
        elif t in ("resumo", "/resumo"):
            st["pedir_resumo"] = True
        elif t == "/start" or bruto.startswith("/"):
            continue
        else:
            lg = idioma_do_texto(bruto, lang)
            st["pendentes"].append({"tipo": "texto", "texto": bruto[:500], "lang": lg,
                                    "palavra": st["ultima_palavra"].get(lg)})
    return confirmacoes


# ---------------------------------------------------------------- Gemini
S = {"type": "STRING"}
SCHEMA_JORNAL = {
    "type": "OBJECT",
    "properties": {
        "titulo_tema": S, "texto": S, "traducao_pt": S,
        "palavra": S, "leitura": S, "sentido_pt": S,
        "exemplo": S, "exemplo_leitura": S, "exemplo_pt": S,
        "pergunta": S, "opcoes": {"type": "ARRAY", "items": S},
        "correta": {"type": "INTEGER"}, "explicacao_pt": S,
    },
    "required": ["titulo_tema", "texto", "traducao_pt", "palavra", "sentido_pt",
                 "exemplo", "exemplo_pt", "pergunta", "opcoes", "correta"],
}
SCHEMA_CORRECAO = {
    "type": "OBJECT",
    "properties": {
        "idioma_ok": {"type": "BOOLEAN"}, "correta": {"type": "BOOLEAN"},
        "corrigida": S, "leitura": S, "explicacao_pt": S, "alternativa": S,
        "nota": {"type": "INTEGER"},
    },
    "required": ["idioma_ok", "correta", "corrigida", "explicacao_pt", "nota"],
}
SCHEMA_VOZ = {
    "type": "OBJECT",
    "properties": {
        "transcricao": S, "inteligibilidade": {"type": "INTEGER"},
        "trechos_problema": {"type": "ARRAY", "items": S}, "dica_pt": S,
    },
    "required": ["transcricao", "inteligibilidade", "dica_pt"],
}


def ler_prompt(nome, lang=None, nivel=None):
    txt = (PROMPTS / f"{nome}.txt").read_text(encoding="utf-8")
    if lang:
        txt = txt.replace("{idioma}", IDIOMA[lang]).replace("{nivel}", nivel or "")
    return txt


def gemini(system, parts, schema):
    if not GEMINI_KEY:
        raise RuntimeError("Secret GEMINI_API_KEY não configurado no GitHub.")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    erro = "sem tentativa"
    for modelo in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        for tentativa in range(3):
            try:
                r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=120)
                if r.status_code in (400, 403, 404):
                    erro = f"{modelo}: HTTP {r.status_code} {r.text[:200]}"
                    break
                if r.status_code in (429, 500, 503):
                    erro = f"{modelo}: HTTP {r.status_code}"
                    time.sleep(15 * (tentativa + 1))
                    continue
                r.raise_for_status()
                raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                out = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                print(f"Gemini ok ({modelo})")
                return out
            except (requests.RequestException, KeyError, IndexError, ValueError) as err:
                erro = f"{modelo}: {err}"
                time.sleep(5)
    raise RuntimeError(f"Gemini falhou — {erro}")


# ---------------------------------------------------------------- correções e voz
def corrigir(st, p, hoje):
    lang = p["lang"]
    nivel = st.get(f"nivel_{lang}", NIVEL_PADRAO[lang])
    r = gemini(ler_prompt("correcao", lang, nivel),
               [{"text": f"Palavra-alvo: {p.get('palavra') or '-'}\nFrase do aluno: {p['texto']}"}],
               SCHEMA_CORRECAO)
    if not r.get("idioma_ok"):
        return None  # conversa em português etc.: ignora sem responder
    nota = max(1, min(5, int(r.get("nota") or 1)))
    registrar(st, "escrita", lang, hoje, ok=bool(r.get("correta")), nota=nota)
    L = [f"✍️ <b>Correção {BANDEIRA[lang]}</b> · nota {nota}/5", f"Você: {e(p['texto'])}"]
    if r.get("correta"):
        L.append("✅ Correta!")
    else:
        L.append(f"➡️ {e(r.get('corrigida', ''))}")
    if lang == "jp" and r.get("leitura"):
        L.append(e(r["leitura"]))
    if r.get("explicacao_pt"):
        L.append(f"💡 {e(r['explicacao_pt'])}")
    alt = r.get("alternativa")
    if alt and alt not in (r.get("corrigida"), p["texto"]):
        L.append(f"🔄 Outra forma: {e(alt)}")
    return "\n".join(L)


def baixar_voz(file_id):
    r = requests.get(f"{TG}/getFile", params={"file_id": file_id}, timeout=20)
    r.raise_for_status()
    fp = r.json()["result"]["file_path"]
    dados = requests.get(f"{TG_FILE}/{fp}", timeout=60).content
    if not TEM_FFMPEG:
        return dados, "audio/ogg"
    src, dst = TMP / "voz_in", TMP / "voz.mp3"
    src.write_bytes(dados)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-ac", "1", "-ar", "16000", "-b:a", "32k", str(dst)], check=True)
    return dst.read_bytes(), "audio/mp3"


def avaliar_voz(st, p, hoje):
    lang = p["lang"]
    nivel = st.get(f"nivel_{lang}", NIVEL_PADRAO[lang])
    alvo = p.get("alvo") or {}
    audio, mime = baixar_voz(p["file_id"])
    r = gemini(ler_prompt("voz", lang, nivel), [
        {"text": f"Frase-alvo: {alvo.get('frase') or '(nenhuma: o aluno falou livremente)'}"},
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(audio).decode()}},
    ], SCHEMA_VOZ)
    nota = max(0, min(5, int(r.get("inteligibilidade") or 0)))
    registrar(st, "voz", lang, hoje, nota=nota)
    L = [f"🎙️ <b>Pronúncia {BANDEIRA[lang]}</b> · clareza {nota}/5"]
    if alvo.get("frase"):
        L.append(f"Alvo: {e(alvo['frase'])}")
    L.append(f"Ouvi: {e(r.get('transcricao', ''))}")
    probs = [x for x in r.get("trechos_problema") or [] if x][:3]
    if probs:
        L.append(f"⚠️ Atenção: {e(' · '.join(probs))}")
    if r.get("dica_pt"):
        L.append(f"💡 {e(r['dica_pt'])}")
    return "\n".join(L)


def processar_pendentes(st, hoje):
    msgs = []
    fila, st["pendentes"] = st["pendentes"][:6], st["pendentes"][6:]
    for p in fila:
        try:
            m = corrigir(st, p, hoje) if p["tipo"] == "texto" else avaliar_voz(st, p, hoje)
            if m:
                msgs.append(m)
        except Exception as err:
            print(f"Aviso: pendente falhou: {err}")
            msgs.append(f"⚠️ Não consegui avaliar {'sua frase' if p['tipo'] == 'texto' else 'seu áudio'}. Tente de novo.")
    return msgs


# ---------------------------------------------------------------- manchetes
def limpar(txt):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", txt or ""))).strip()


def watchlist():
    if not WATCHLIST_PATH.exists():
        return []
    return [t.strip().upper() for t in WATCHLIST_PATH.read_text(encoding="utf-8").split() if t.strip()]


def urls_do_tema(tema):
    if tema != "bolsa":
        return FEEDS[tema], ""
    tickers = watchlist()
    if not tickers:
        return [gnews("Wall Street S&P 500")], ""
    yahoo = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=" + ",".join(tickers) + "&region=US&lang=en-US"
    return [yahoo, gnews(" OR ".join(tickers) + " ações")], "Watchlist do leitor: " + ", ".join(tickers)


def manchetes(st, tema):
    urls, extra = urls_do_tema(tema)
    usados = set(st["links_usados"])
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            entries = feedparser.parse(r.content).entries
        except Exception as err:
            print(f"Aviso: feed falhou {url[:80]}: {err}")
            continue
        itens = [(limpar(x.get("title")), x.get("link", ""), limpar(x.get("summary"))[:300])
                 for x in entries if x.get("title")]
        if itens:
            novos = [i for i in itens if i[1] not in usados] or itens
            return extra, novos[:5]
    return extra, []


def gerar_texto(st, lang, tema, nivel, evitar):
    """Retorna (item, itens_de_fonte, obra)."""
    obra, itens, extra = None, [], ""
    if tema == "literatura":
        lista = OBRAS[lang]
        obra = lista[st["obra_idx"].get(lang, 0) % len(lista)]
        fontes = f"Obra: {obra}"
    else:
        extra, itens = manchetes(st, tema)
        fontes = "Manchetes e resumos:\n" + ("\n".join(f"- {t}\n  {s}" for t, _, s in itens)
                                             or "(nenhuma manchete hoje: escreva 2 frases neutras sobre o tema, sem fatos específicos)")
    user = (f"Tema: {tema}\nNível: {nivel}\n{extra}\n{fontes}\n"
            f"Não use como palavra do dia: {', '.join(evitar) or '-'}")
    item = gemini(ler_prompt(lang), [{"text": user}], SCHEMA_JORNAL)
    if not item.get("texto") or not item.get("palavra"):
        raise RuntimeError("Gemini devolveu JSON sem texto/palavra")
    return item, itens, obra


# ---------------------------------------------------------------- áudio
def sem_furigana(t):
    return re.sub(r"[（(][ぁ-んァ-ンー・]+[）)]", "", t)


def tts(texto, lang, path, lento=False):
    from gtts import gTTS
    gTTS(texto, lang="ja" if lang == "jp" else "de", slow=lento).save(str(path))
    return path


def frases(texto, lang):
    t = sem_furigana(texto)
    partes = re.split(r"(?<=[。！？])", t) if lang == "jp" else re.split(r"(?<=[.!?])\s+", t)
    return [s.strip() for s in partes if len(s.strip()) > 1][:8]


def duracao(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def silencio(seg, path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", f"{seg:.1f}", "-c:a", "libmp3lame", "-b:a", "32k", str(path)], check=True)
    return path


def audio_shadowing(texto, lang):
    """Cada frase: lenta → pausa para repetir → normal → pausa para repetir."""
    if not TEM_FFMPEG:
        return None
    partes = []
    for i, f in enumerate(frases(texto, lang)):
        lenta = tts(f, lang, TMP / f"sh{i}_l.mp3", lento=True)
        normal = tts(f, lang, TMP / f"sh{i}_n.mp3")
        pausa = silencio(duracao(normal) * 1.3 + 0.8, TMP / f"sh{i}_p.mp3")
        partes += [lenta, pausa, normal, pausa]
    if not partes:
        return None
    lista = TMP / "sh_lista.txt"
    lista.write_text("".join(f"file '{p}'\n" for p in partes), encoding="utf-8")
    out = TMP / f"shadowing_{lang}.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
                    "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "48k", str(out)], check=True)
    return out


def gerar_audios(item, lang, titulo):
    if (os.environ.get("SEM_AUDIO") or "").lower() == "true":
        return []
    lista = []
    try:
        texto = sem_furigana(item["texto"])
        if item.get("exemplo"):
            texto += ("。\n" if lang == "jp" else "\n\n") + item["exemplo"]
        lista.append((tts(texto, lang, TMP / f"jornal_{lang}.mp3"), f"{titulo} ▶️"))
    except Exception as err:
        print(f"Aviso: áudio normal falhou: {err}")
    try:
        sh = audio_shadowing(item["texto"], lang)
        if sh:
            lista.append((sh, f"{titulo} · shadowing 🔁"))
    except Exception as err:
        print(f"Aviso: shadowing falhou: {err}")
    return lista


# ---------------------------------------------------------------- mensagem
def nucleo(palavra):
    p = re.split(r"[,(]", palavra)[0].strip()
    return re.sub(r"^(der|die|das)\s+", "", p, flags=re.I)


def lacuna(p):
    ex, alvo = p.get("exemplo") or "", nucleo(p["palavra"])
    return ex.replace(alvo, "＿＿＿") if alvo and alvo in ex else None


def montar(item, lang, hora, tema, nivel, revisao, confirmacoes, link, obra):
    jp = lang == "jp"
    titulo = item.get("titulo_tema") or TITULO[(lang, tema)]
    L = []
    if confirmacoes:
        L += [e(" · ".join(confirmacoes)), ""]
    L.append(f"{BANDEIRA[lang]} <b>{e(hora)} · {e(titulo)}</b> · {e(nivel)}")
    if obra:
        L.append(f"📖 {e(obra)}")
    L += ["", e(item["texto"])]
    if item.get("traducao_pt"):
        L += ["", f"🇧🇷 <tg-spoiler>{e(item['traducao_pt'])}</tg-spoiler>"]

    leit = f"（{item['leitura']}）" if jp and item.get("leitura") else ""
    L += ["", f"📌 <b>{'今日の単語' if jp else 'Wort des Tages'}</b>",
          f"{e(item['palavra'])}{e(leit)} — {e(item['sentido_pt'])}"]
    if item.get("exemplo"):
        L.append(f"<i>{e(item['exemplo'])}</i>")
        if jp and item.get("exemplo_leitura"):
            L.append(e(item["exemplo_leitura"]))
        if item.get("exemplo_pt"):
            L.append(f"→ {e(item['exemplo_pt'])}")

    if revisao:
        resp = revisao["palavra"] + (f"（{revisao['leitura']}）" if jp and revisao.get("leitura") else "")
        frase = lacuna(revisao)
        L += ["", f"🔁 <b>{'復習' if jp else 'Wiederholung'}</b> ({revisao['data'][8:10]}/{revisao['data'][5:7]})"]
        L.append(e(frase) if frase else f"Como se diz: {e(revisao['sentido_pt'])}?")
        if frase:
            L.append(f"({e(revisao['sentido_pt'])})")
        L += [f"Resposta: <tg-spoiler>{e(resp)}</tg-spoiler>", "👉 soube / não soube"]

    L += ["", f"✍️ Mande 1 frase com <b>{e(nucleo(item['palavra']))}</b> · 🎙️ grave o exemplo em voz"]
    if link:
        L.append(f'🔗 <a href="{e(link, quote=True)}">fonte</a>')
    L.append("💬 fácil / difícil · hoje sem bot · resumo")
    return "\n".join(L)


def resumo(st, hoje):
    def janela(a, b):
        ini, fim = (hoje - timedelta(days=a)).isoformat(), (hoje - timedelta(days=b)).isoformat()
        return [x for x in st["log"] if ini <= x["d"] <= fim]

    def taxa(lst, *tipos):
        oks = [x["ok"] for x in lst if x["t"] in tipos and x.get("ok") is not None]
        return sum(oks), len(oks)

    def media(lst, t):
        ns = [x["nota"] for x in lst if x["t"] == t and x.get("nota") is not None]
        return (sum(ns) / len(ns), len(ns)) if ns else (None, 0)

    def fmt(ac, tot):
        return f"{ac}/{tot} ({round(100 * ac / tot)}%)" if tot else "—"

    def num(v):
        return f"{v:.1f}".replace(".", ",")

    atual, anterior = janela(6, 0), janela(13, 7)
    L = [f"📊 <b>Resumo da semana</b> · {hoje - timedelta(days=6):%d/%m}–{hoje:%d/%m}"]
    for lang, nome in (("jp", "🇯🇵 Japonês"), ("de", "🇩🇪 Alemão")):
        a = [x for x in atual if x["lang"] == lang]
        b = [x for x in anterior if x["lang"] == lang]
        L += ["", f"<b>{nome}</b> · nível {st[f'nivel_{lang}']}",
              f"🆕 Palavras novas: {sum(1 for x in a if x['t'] == 'nova')}",
              f"🔁 Revisões: {fmt(*taxa(a, 'rev'))}",
              f"❓ Quiz: {fmt(*taxa(a, 'quiz'))}"]
        m, n = media(a, "escrita")
        if n:
            L.append(f"✍️ Frases: {n} · nota média {num(m)}/5")
        m, n = media(a, "voz")
        if n:
            L.append(f"🎙️ Gravações: {n} · clareza média {num(m)}/5")
        ac, tot = taxa(a, "rev", "quiz")
        ac_b, tot_b = taxa(b, "rev", "quiz")
        if tot and tot_b:
            d = round(100 * ac / tot) - round(100 * ac_b / tot_b)
            L.append(f"{'📈' if d >= 0 else '📉'} Acerto geral {round(100 * ac / tot)}% ({'+' if d >= 0 else ''}{d} p.p. vs semana anterior)")
        L.append(f"🏆 Palavras dominadas (total): {st['dominadas'].get(lang, 0)}")
    return "\n".join(L)


# ---------------------------------------------------------------- main
def main():
    st = load_state()
    hoje_real = datetime.now(TZ).date()
    confirmacoes = processar_updates(st)
    respostas = processar_pendentes(st, hoje_real)

    if MODO == "respostas":
        if confirmacoes:
            enviar_texto(e(" · ".join(confirmacoes)))
        for m in respostas:
            enviar_texto(m)
        if st.get("pedir_resumo"):
            enviar_texto(resumo(st, hoje_real))
            st["pedir_resumo"] = False
        return save_state(st)

    for m in respostas:
        enviar_texto(m)

    dia, hora, lang, k = escolher_slot()
    tema = tema_do_dia(dia, lang, k)
    hoje = dia.isoformat()
    chave = f"{hoje}-{hora}"
    manual = bool((os.environ.get("SLOT_HORA") or "").strip())
    domingo_fim = dia.weekday() == 6 and hora == SLOTS[-1][0]

    pular = st.get("skip_date") == hoje or (chave in st["enviados"] and not manual)
    if pular:
        print("Pausado hoje." if st.get("skip_date") == hoje else f"Slot {chave} já enviado.")
        if confirmacoes:
            enviar_texto(e(" · ".join(confirmacoes)))
        if st.get("pedir_resumo"):
            enviar_texto(resumo(st, hoje_real))
            st["pedir_resumo"] = False
        return save_state(st)

    nivel = st.get(f"nivel_{lang}", NIVEL_PADRAO[lang])
    evitar = [p["palavra"] for p in st["palavras"] if p.get("lang") == lang][-30:]
    revisao = next(iter(sorted(
        (p for p in st["palavras"] if p.get("lang") == lang and not p.get("dominada")
         and p.get("data", "") < hoje and p.get("proxima", hoje) <= hoje),
        key=lambda p: (p.get("tema") != tema, p.get("proxima", ""), p.get("data", "")))), None)

    item, itens, obra = gerar_texto(st, lang, tema, nivel, evitar)
    titulo = item.get("titulo_tema") or TITULO[(lang, tema)]
    link = itens[0][1] if itens else ""

    enviar_texto(montar(item, lang, hora, tema, nivel, revisao, confirmacoes, link, obra))
    enviar_audios(gerar_audios(item, lang, titulo), lang)
    poll_id = enviar_quiz(item)
    if poll_id:
        st["quizzes"][poll_id] = {"correta": item["correta"], "lang": lang, "data": hoje}

    st["ultimo_lang"] = lang
    if revisao:
        st["revisao_aberta"] = revisao["id"]
    if obra:
        st["obra_idx"][lang] = st["obra_idx"].get(lang, 0) + 1
    st["ultima_palavra"][lang] = nucleo(item["palavra"])
    st["ultima_frase"][lang] = {"frase": item.get("exemplo", ""), "leitura": item.get("exemplo_leitura", "")}
    st["enviados"].append(chave)
    st["links_usados"] += [i[1] for i in itens if i[1]]
    st["palavras"].append({
        "id": f"{hoje}-{hora}-{lang}",
        "palavra": item["palavra"], "leitura": item.get("leitura", ""),
        "sentido_pt": item["sentido_pt"], "exemplo": item.get("exemplo", ""),
        "lang": lang, "tema": tema, "data": hoje,
        "proxima": (dia + timedelta(days=1)).isoformat(), "acertos": 0,
    })
    registrar(st, "nova", lang, dia)

    if domingo_fim or st.get("pedir_resumo"):
        enviar_texto(resumo(st, hoje_real))
        st["pedir_resumo"] = False
    save_state(st)


if __name__ == "__main__":
    main()
