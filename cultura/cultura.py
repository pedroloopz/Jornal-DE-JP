"""
DIARIO CULTURAL - pintura do dia e obra musical do dia no Telegram.

  PINTURA: obra em dominio publico do Art Institute of Chicago, com pintor,
           ano, movimento e a historia por tras (base: texto do museu).
  MUSICA:  obra classica da lista em musicas.py, com link do YouTube e a
           historia por tras (base: Wikipedia).

O Gemini so reconta o texto das fontes em portugues; nao inventa fatos.
Uso: TIPO=pintura|musica python cultura/cultura.py
"""

import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).parent))
from musicas import OBRAS  # noqa: E402

AQUI = Path(__file__).parent
ESTADO = AQUI / "state.json"
TZ = ZoneInfo("America/Sao_Paulo")
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = str(os.environ["TELEGRAM_CHAT_ID"]).strip()
TG = f"https://api.telegram.org/bot{TOKEN}"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODELS = list(dict.fromkeys(m for m in [
    os.environ.get("GEMINI_MODEL", "").strip(), "gemini-3.5-flash-lite", "gemini-flash-latest"] if m))
UA = {"User-Agent": "Mozilla/5.0 (diario-cultural; github.com/pedroloopz)",
      "AIC-User-Agent": "diario-cultural (github.com/pedroloopz)"}
e = html.escape


# ================================================================ ESTADO
def carregar():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {"pinturas": [], "musicas": []}


def salvar(st):
    ESTADO.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ================================================================ GEMINI
def gemini(instrucao, texto, schema):
    if not GEMINI_KEY:
        raise RuntimeError("Secret GEMINI_API_KEY nao configurado.")
    body = {"systemInstruction": {"parts": [{"text": instrucao}]},
            "contents": [{"role": "user", "parts": [{"text": texto}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                 "temperature": 0.4}}
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
                return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            except (requests.RequestException, KeyError, IndexError, ValueError) as err:
                erro = f"{modelo}: {err}"
                time.sleep(5)
    raise RuntimeError(f"Gemini falhou - {erro}")


REGRAS = ("Voce escreve para um canal de Telegram em portugues do Brasil, para um leitor culto e curioso. "
          "Use SOMENTE fatos presentes nas fontes fornecidas. Se as fontes nao trouxerem uma historia, "
          "descreva o contexto que elas trazem e nao invente anedotas, datas, nomes ou citacoes. "
          "Tom: claro, vivo, sem exageros, sem emojis, sem hashtags, sem markdown.")


# ================================================================ TELEGRAM
def tg(metodo, base=TG, **kw):
    r = requests.post(f"{base}/{metodo}", timeout=60, **kw)
    if not r.ok:
        raise RuntimeError(f"Telegram recusou ({metodo}): {r.text[:300]}")
    return r.json()


def limitar(txt, n):
    return txt if len(txt) <= n else txt[: n - 1].rsplit(" ", 1)[0] + "…"


# ================================================================ WIKIPEDIA
def wiki(lang, busca):
    """Titulo e texto (ate ~5000 caracteres) do melhor resultado da busca."""
    base = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        r = requests.get(base, headers=UA, timeout=30, params={
            "action": "query", "list": "search", "srsearch": busca, "srlimit": 1, "format": "json"}).json()
        hits = r.get("query", {}).get("search", [])
        if not hits:
            return None, ""
        titulo = hits[0]["title"]
        r = requests.get(base, headers=UA, timeout=30, params={
            "action": "query", "prop": "extracts", "explaintext": 1, "exchars": 5000,
            "titles": titulo, "format": "json"}).json()
        pag = next(iter(r["query"]["pages"].values()))
        return titulo, pag.get("extract", "")
    except Exception as err:
        print("wikipedia falhou:", err)
        return None, ""


# ================================================================ PINTURA
CAMPOS = ["id", "title", "artist_title", "artist_display", "date_display", "date_end", "style_title",
          "place_of_origin", "medium_display", "image_id", "description", "short_description"]


def aic_busca(filtros, pagina, limite=100):
    q = {"query": {"bool": {"must": filtros}}, "fields": CAMPOS, "limit": limite, "page": pagina}
    url = "https://api.artic.edu/api/v1/artworks/search"
    r = requests.post(url, headers=UA, json=q, timeout=60)
    if not r.ok:
        r = requests.get(url, headers=UA, params={"params": json.dumps(q)}, timeout=60)
    r.raise_for_status()
    return r.json()


def escolher_pintura(st):
    base = [{"term": {"is_public_domain": True}}, {"term": {"artwork_type_id": 1}},
            {"exists": {"field": "image_id"}}]
    for filtros in (base + [{"term": {"is_boosted": True}}], base):   # 1o: obras em destaque do museu
        total = aic_busca(filtros, 1, 1)["pagination"]["total"]
        if total < 30:
            continue
        paginas = min(total // 100, 10) or 1
        for _ in range(4):
            dados = aic_busca(filtros, random.randint(1, paginas))["data"]
            novas = [d for d in dados if d["id"] not in st["pinturas"] and d.get("image_id")]
            com_texto = [d for d in novas if d.get("description") or d.get("short_description")]
            if com_texto or novas:
                return random.choice(com_texto or novas)
    raise RuntimeError("Nenhuma pintura nova encontrada no Art Institute of Chicago.")


def canal_pintura(st):
    """Bot proprio da pintura (segredo PINTURA_TELEGRAM_TOKEN). Destino: o canal onde o bot
    e administrador; enquanto nao houver canal, a conversa privada de quem deu /start.
    Descoberto sozinho e salvo no estado; se um canal aparecer depois, passa a usar o canal."""
    token = os.environ.get("PINTURA_TELEGRAM_TOKEN", "").strip()
    if not token:
        return TG, CHAT
    base = f"https://api.telegram.org/bot{token}"
    fixo = os.environ.get("PINTURA_CHAT_ID", "").strip()
    if fixo:
        return base, fixo
    atual = st.get("canal_pintura")
    if isinstance(atual, int) or (isinstance(atual, dict) and atual.get("tipo") == "canal"):
        return base, atual if isinstance(atual, int) else atual["id"]
    try:
        ups = requests.get(f"{base}/getUpdates", timeout=30, params={"allowed_updates": json.dumps(
            ["message", "my_chat_member", "channel_post"])}).json().get("result", [])
    except Exception:
        ups = []
    for u in ups:
        mcm = u.get("my_chat_member") or {}
        chat = (mcm or u.get("channel_post") or u.get("message") or {}).get("chat", {})
        if chat.get("type") == "channel" and (not mcm or mcm.get("new_chat_member", {}).get("status") == "administrator"):
            st["canal_pintura"] = {"id": chat["id"], "tipo": "canal", "nome": chat.get("title", "")}
        elif chat.get("type") == "private" and not (st.get("canal_pintura") or {}).get("tipo") == "canal":
            st["canal_pintura"] = {"id": chat["id"], "tipo": "privado", "nome": chat.get("first_name", "")}
    destino = st.get("canal_pintura")
    if destino:
        print("pintura vai para:", destino)
        return base, destino["id"]
    nome = requests.get(f"{base}/getMe", timeout=30).json().get("result", {}).get("username", "?")
    raise RuntimeError(f"Bot da pintura sem destino. Mande /start para @{nome} ou adicione-o como "
                       "administrador do canal (com permissao de publicar) e rode de novo.")


def seculo(ano):
    if not ano:
        return ""
    s = (int(ano) - 1) // 100 + 1
    romanos = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII",
               "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI"]
    return f"séc. {romanos[s]}" if 0 < s < len(romanos) else ""


def pintura(st):
    p = escolher_pintura(st)
    desc = re.sub(r"<[^>]+>", " ", p.get("description") or p.get("short_description") or "")
    _, sobre_artista = wiki("pt", p.get("artist_title") or "") if p.get("artist_title") else (None, "")
    fontes = (f"OBRA: {p['title']}\nARTISTA: {p.get('artist_display')}\nDATA: {p.get('date_display')}\n"
              f"ESTILO (catalogo): {p.get('style_title')}\nORIGEM: {p.get('place_of_origin')}\n"
              f"TECNICA: {p.get('medium_display')}\n\nTEXTO DO MUSEU:\n{desc[:3000]}\n\n"
              f"WIKIPEDIA SOBRE O ARTISTA:\n{sobre_artista[:2500]}")
    out = gemini(REGRAS + " Escreva sobre UMA pintura.", fontes, {
        "type": "OBJECT", "required": ["titulo_pt", "movimento_pt", "historia"], "properties": {
            "titulo_pt": {"type": "STRING", "description": "titulo traduzido para o portugues"},
            "movimento_pt": {"type": "STRING", "description": "movimento/escola em portugues; vazio se as fontes nao disserem"},
            "historia": {"type": "STRING", "description": "3 a 4 frases: o que a obra mostra e a historia ou o contexto por tras dela"}}})

    img = requests.get(f"https://www.artic.edu/iiif/2/{p['image_id']}/full/1686,/0/default.jpg",
                       headers=UA, timeout=60)
    if not img.ok:
        img = requests.get(f"https://www.artic.edu/iiif/2/{p['image_id']}/full/843,/0/default.jpg",
                           headers=UA, timeout=60)
    img.raise_for_status()

    titulo = out["titulo_pt"].strip() or p["title"]
    original = f" <i>({e(p['title'])})</i>" if titulo.lower() != p["title"].lower() else ""
    era = " · ".join(x for x in [out.get("movimento_pt", "").strip(), seculo(p.get("date_end"))] if x)
    cabecalho = (f"🎨 <b>Pintura do dia</b>\n\n<b>{e(titulo)}</b>{original}\n"
                 f"{e(p.get('artist_title') or 'Artista desconhecido')}, {e(p.get('date_display') or 's/d')}\n"
                 + (f"{e(era)}\n" if era else ""))
    rodape = f'\n<a href="https://www.artic.edu/artworks/{p["id"]}">Art Institute of Chicago</a>'
    espaco = 1024 - len(re.sub(r"<[^>]+>", "", cabecalho + rodape)) - 30
    legenda = cabecalho + f"\n<blockquote>{e(limitar(out['historia'].strip(), espaco))}</blockquote>" + rodape
    base, canal = canal_pintura(st)
    tg("sendPhoto", base=base, data={"chat_id": canal, "caption": legenda, "parse_mode": "HTML"},
       files={"photo": ("pintura.jpg", img.content, "image/jpeg")})
    st["pinturas"].append(p["id"])
    print("pintura enviada:", p["id"], p["title"])


# ================================================================ MUSICA
def youtube(busca):
    """Primeiro video da busca no YouTube, conferido pelo oEmbed. None se falhar."""
    try:
        r = requests.get("https://www.youtube.com/results", params={"search_query": busca},
                         headers={**UA, "Accept-Language": "pt-BR,pt;q=0.9"},
                         cookies={"CONSENT": "YES+1"}, timeout=30)
        for vid in dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', r.text)):
            url = f"https://www.youtube.com/watch?v={vid}"
            o = requests.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=20)
            if o.ok:
                return url, o.json().get("title", "")
    except Exception as err:
        print("youtube falhou:", err)
    return None, ""


def musica(st):
    livres = [i for i in range(len(OBRAS)) if i not in st["musicas"]]
    if not livres:                      # passou por todas: recomeca o rodizio
        st["musicas"], livres = [], list(range(len(OBRAS)))
    i = random.choice(livres)
    compositor, obra, ano, periodo = OBRAS[i]
    sobrenome = compositor.split()[-1]
    _, pt = wiki("pt", f"{obra} {sobrenome}")
    _, en = wiki("en", f"{obra} {sobrenome}")
    fontes = (f"OBRA: {obra}\nCOMPOSITOR: {compositor}\nANO: {ano}\nPERIODO: {periodo}\n\n"
              f"WIKIPEDIA (PT):\n{pt[:3000]}\n\nWIKIPEDIA (EN):\n{en[:3000]}")
    out = gemini(REGRAS + " Escreva sobre UMA obra de musica classica. Se um texto da Wikipedia for "
                 "sobre outra obra, ignore esse texto.", fontes, {
        "type": "OBJECT", "required": ["historia", "ouvir"], "properties": {
            "historia": {"type": "STRING", "description": "4 a 5 frases: como e por que a obra nasceu e a historia por tras dela"},
            "ouvir": {"type": "STRING", "description": "1 frase: um detalhe para prestar atencao ao ouvir, tirado das fontes; vazio se nao houver"}}})

    link, titulo_video = youtube(f"{compositor} {obra}")
    if not link:
        link = "https://www.youtube.com/results?search_query=" + requests.utils.quote(f"{compositor} {obra}")
    texto = (f"🎼 <b>Obra musical do dia</b>\n\n<b>{e(obra)}</b>\n{e(compositor)}, {e(ano)} · {e(periodo)}\n\n"
             f"{e(out['historia'].strip())}\n"
             + (f"\n🎧 <i>{e(out['ouvir'].strip())}</i>\n" if out.get("ouvir", "").strip() else "")
             + f'\n▶️ <a href="{link}">Ouvir no YouTube</a>')
    tg("sendMessage", json={"chat_id": CHAT, "text": texto[:4000], "parse_mode": "HTML",
                            "link_preview_options": {"url": link, "prefer_large_media": True}})
    st["musicas"].append(i)
    print("musica enviada:", obra, "|", titulo_video or "busca")


# ================================================================ MAIN
def main():
    tipo = (os.environ.get("TIPO") or "").strip()
    if not tipo:                                   # pelo horario: manha = pintura, noite = musica
        tipo = "pintura" if datetime.now(TZ).hour < 15 else "musica"
    st = carregar()
    (pintura if tipo == "pintura" else musica)(st)
    salvar(st)


if __name__ == "__main__":
    main()
