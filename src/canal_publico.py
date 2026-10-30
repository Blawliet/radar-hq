"""Canal PÚBLICO de ofertas Nesta Estante (Canal do WhatsApp), com a tag de afiliado.

A API oficial do WhatsApp não posta em Canais, e automatizar um número comum arrisca
banimento. Então o radar faz o resto: a cada rodada, manda para o Bruno, numa conversa
PRIVADA com o bot do Telegram, cada post pronto no formato do WhatsApp (negrito com *,
riscado com ~). Um toque no bloco copia; é só colar no Canal.

Separado do grupo dos amigos (BASTA LER): aqui não entram nomes nem listas pessoais.
Config em config/canal_publico.yaml. Secrets: NESTA_RASCUNHO_CHAT (o ID do Bruno no
Telegram, para onde vão os rascunhos) e AMAZON_TAG (a tag de Associado da Amazon).

O que é postado: ofertas da Amazon (das buscas das listas e das buscas próprias do
canal) com desconto de pelo menos `desconto_minimo`, edição brasileira, com estoque.
Cada produto é postado no máximo uma vez a cada `repostar_dias`, a não ser que o
preço caia de novo.
"""
from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from texto import contem, normalizar

log = logging.getLogger("canal")
RAIZ = Path(__file__).resolve().parents[1]
_lock = threading.Lock()


def config() -> dict:
    caminho = RAIZ / "config" / "canal_publico.yaml"
    try:
        cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        cfg = {}
    cfg["chat"] = os.getenv("NESTA_RASCUNHO_CHAT") or cfg.get("chat") or ler_chat_salvo()
    cfg["tag"] = os.getenv("AMAZON_TAG") or cfg.get("tag") or ""
    cfg.setdefault("desconto_minimo", 30)
    cfg.setdefault("horarios_post", ["09:30", "13:30", "19:30"])
    cfg.setdefault("posts_por_horario", 1)
    cfg.setdefault("repostar_dias", 7)
    cfg.setdefault("buscas_por_rodada", 3)
    return cfg


# ------------------------------------------------------------------ conversa privada com o bot
# O Bruno abre o link t.me/<bot>?start=nesta-<código> e o radar descobre sozinho para onde
# mandar os rascunhos. O ID fica salvo CIFRADO com o token do bot (o repositório é público).
ARQ_CHAT = RAIZ / "data" / "nesta-chat.bin"


def _chave() -> bytes:
    import hashlib
    return hashlib.sha256(("nesta:" + os.getenv("TELEGRAM_BOT_TOKEN", "")).encode()).digest()


def _xor(dados: bytes) -> bytes:
    k = _chave()
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(dados))


def ler_chat_salvo() -> str:
    try:
        texto = _xor(ARQ_CHAT.read_bytes()).decode()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""
    return texto[4:] if texto.startswith("chat") else ""


def descobrir_chat(cfg: dict) -> str:
    """Procura nas mensagens recebidas pelo bot o /start com o código do Nesta Estante."""
    import hashlib
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    alvo = cfg.get("codigo_hash")
    if not token or not alvo:
        return ""
    import httpx
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20,
                      params={"allowed_updates": '["message"]'})
        updates = r.json().get("result", []) if r.status_code == 200 else []
    except Exception as e:
        log.warning("Canal público: não consegui ler as mensagens do bot (%s)", e)
        return ""
    for u in reversed(updates):
        msg = u.get("message") or {}
        partes = (msg.get("text") or "").split()
        if msg.get("chat", {}).get("type") != "private" or len(partes) < 2 or partes[0] != "/start":
            continue
        if hashlib.sha256(partes[1].encode()).hexdigest() != alvo:
            continue
        chat = str(msg["chat"]["id"])
        ARQ_CHAT.parent.mkdir(parents=True, exist_ok=True)
        ARQ_CHAT.write_bytes(_xor(("chat" + chat).encode()))
        try:
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": chat, "text": "✅ Pronto! Os rascunhos do Nesta Estante vão chegar aqui, "
                                        "às 09h30, 13h30 e 19h30. É só tocar no bloco para copiar."})
        except Exception:
            pass
        log.info("Canal público: conversa privada encontrada e salva.")
        return chat
    return ""


def ativo(cfg: dict | None = None) -> bool:
    cfg = cfg or config()
    return bool(cfg.get("ativo"))


def buscas_da_rodada(cfg: dict, janela: int | None = None) -> list[str]:
    """Reveza as buscas próprias do canal: algumas por rodada, para não pesar na Amazon."""
    import time
    todas = list(cfg.get("buscas") or [])
    if not todas:
        return []
    n = max(1, int(cfg.get("buscas_por_rodada", 3)))
    janela = int(time.time() // 7200) if janela is None else janela
    ini = (janela * n) % len(todas)
    return [todas[(ini + i) % len(todas)] for i in range(min(n, len(todas)))]


def desconto(o) -> float | None:
    if o.preco and o.preco_lista and o.preco_lista > o.preco:
        return round((1 - o.preco / o.preco_lista) * 100, 1)
    return None


def link_afiliado(o, tag: str) -> str:
    asin = o.extra.get("asin") or (re.search(r"/dp/([A-Z0-9]{10})", o.url) or [None, None])[1]
    if not asin:
        return o.url
    return f"https://www.amazon.com.br/dp/{asin}" + (f"?tag={tag}" if tag else "")


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_post(o, tag: str) -> str:
    """Texto do post no formato do WhatsApp."""
    d = desconto(o)
    linhas = [f"📚 *{o.titulo[:180].replace('*', '')}*", ""]
    if o.preco_lista and d:
        linhas.append(f"De ~{_brl(o.preco_lista)}~ por *{_brl(o.preco)}* ({d:.0f}% OFF)")
    else:
        linhas.append(f"Por *{_brl(o.preco)}*")
    if o.vendedor and "amazon" not in o.vendedor.lower():
        linhas.append(f"Vendido por {o.vendedor[:60]}")
    linhas += ["", f"🛒 {link_afiliado(o, tag)}", "", "_Preço e estoque podem mudar a qualquer momento._"]
    if tag:
        linhas.append("_Como Associado da Amazon, recebo por compras qualificadas._")
    return "\n".join(linhas)


def mensagem_rascunho(post: str, n: int, total: int) -> str:
    """Mensagem do bot: o post dentro de um bloco que copia com um toque."""
    return (f"📝 <b>Nesta Estante</b> · post {n} de {total}\n"
            f"Toque no bloco para copiar e cole no Canal do WhatsApp.\n\n"
            f"<pre>{html.escape(post)}</pre>")


# ------------------------------------------------------------------ memória dos posts
def _con(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS canal_posts (chave TEXT PRIMARY KEY, preco REAL, quando TEXT, titulo TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS canal_candidatos (chave TEXT PRIMARY KEY, desconto REAL, visto TEXT, dados TEXT)")
    return con


def ja_postado(con, chave: str, preco: float, dias: int) -> bool:
    row = con.execute("SELECT preco, quando FROM canal_posts WHERE chave=?", (chave,)).fetchone()
    if not row:
        return False
    if preco < row[0] * 0.97:
        return False                    # caiu de novo: vale postar
    return datetime.fromisoformat(row[1]) > datetime.now() - timedelta(days=dias)


def selecionar(validadas: list, cfg: dict, excluir_titulos: list[str]) -> list:
    minimo = float(cfg.get("desconto_minimo", 30))
    vistos, saida = set(), []
    for o in validadas:
        if not o.loja.startswith("Amazon") or "via @" in o.loja or not o.disponivel:
            continue
        if o.condicao == "usado":
            continue
        d = desconto(o)
        if d is None or d < minimo or d > 85:          # > 85% costuma ser preço "de" inflado
            continue
        n = normalizar(o.titulo)
        if any(contem(n, x) for x in excluir_titulos):
            continue
        chave = o.extra.get("asin") or o.url
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(o)
    saida.sort(key=lambda o: -(desconto(o) or 0))
    return saida


def _guardar_candidatos(con, cands: list):
    """Junta as ofertas boas de cada rodada; o post do horário escolhe a melhor do período."""
    import json
    agora = datetime.now().isoformat(timespec="seconds")
    with _lock:
        for o in cands:
            dados = {"titulo": o.titulo, "url": o.url, "preco": o.preco, "preco_lista": o.preco_lista,
                     "vendedor": o.vendedor, "asin": o.extra.get("asin")}
            con.execute("INSERT OR REPLACE INTO canal_candidatos VALUES (?,?,?,?)",
                        (o.extra.get("asin") or o.url, desconto(o) or 0, agora, json.dumps(dados, ensure_ascii=False)))
        con.execute("DELETE FROM canal_candidatos WHERE visto < ?",
                    ((datetime.now() - timedelta(days=2)).isoformat(timespec="seconds"),))
        con.commit()


def horario_da_vez(cfg: dict, agora: datetime | None = None) -> str | None:
    """'13:30' se esta rodada cai num horário de post (até 60 min de diferença), senão None."""
    agora = agora or datetime.now()
    for h in cfg.get("horarios_post") or []:
        hh, mm = (int(x) for x in str(h).split(":"))
        alvo = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((agora - alvo).total_seconds()) <= 60 * 60:
            return f"{hh:02d}:{mm:02d}"
    return None


def publicar(validadas: list, excluir_titulos: list[str], enviar: bool = True,
             db: Path | None = None, agora: datetime | None = None) -> int:
    """Guarda as ofertas boas de toda rodada; nos horários de post, manda o rascunho da
    melhor oferta das últimas horas (que ainda não foi postada)."""
    import json
    from models import Oferta
    cfg = config()
    if not ativo(cfg):
        return 0
    agora = agora or datetime.now()
    if enviar and not cfg["chat"]:
        cfg["chat"] = descobrir_chat(cfg)
    con = _con(db or RAIZ / "data" / "radar.db")
    _guardar_candidatos(con, selecionar(validadas, cfg, excluir_titulos))
    slot = horario_da_vez(cfg, agora)
    if not slot:
        return 0
    marca = f"horario:{agora.date()} {slot}"
    with _lock:
        if con.execute("SELECT 1 FROM canal_posts WHERE chave=?", (marca,)).fetchone():
            return 0                                   # este horário já foi usado hoje
        desde = (agora - timedelta(hours=float(cfg.get("janela_horas", 8)))).isoformat(timespec="seconds")
        linhas = con.execute("SELECT chave, dados FROM canal_candidatos WHERE visto >= ? ORDER BY desconto DESC",
                             (desde,)).fetchall()
    escolhidos = []
    for chave, dados in linhas:
        d = json.loads(dados)
        o = Oferta("Amazon Brasil", d["titulo"], d["url"], d["preco"], preco_lista=d["preco_lista"],
                   vendedor=d.get("vendedor"), disponivel=True, extra={"asin": d.get("asin")})
        with _lock:
            if ja_postado(con, chave, o.preco, int(cfg["repostar_dias"])):
                continue
        escolhidos.append((chave, o))
        if len(escolhidos) >= int(cfg.get("posts_por_horario", 1)):
            break
    if not escolhidos:
        log.info("Canal público: horário %s sem oferta nova com %s%%+.", slot, cfg["desconto_minimo"])
        return 0
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    postados = 0
    for chave, o in escolhidos:
        post = montar_post(o, cfg["tag"])
        if not enviar or not cfg["chat"] or not token:
            if not cfg["chat"] and enviar:
                log.warning("Canal público: falta o Secret NESTA_RASCUNHO_CHAT; só mostrando no log.")
            print("\n[Nesta Estante]\n" + post + "\n" + "-" * 40)
            if not os.getenv("PYTEST_CURRENT_TEST"):
                arq = RAIZ / "data" / "debug" / "nesta-estante-rascunhos.txt"
                arq.parent.mkdir(parents=True, exist_ok=True)
                with arq.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{agora:%d/%m %H:%M}] horário {slot}\n{post}\n" + "-" * 40 + "\n")
            postados += 1
            continue
        import httpx
        r = None
        try:
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": cfg["chat"], "text": mensagem_rascunho(post, postados + 1, len(escolhidos)),
                "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}})
            ok = r.status_code == 200 and r.json().get("ok")
        except Exception as e:
            log.warning("Canal público: falha ao enviar o rascunho (%s)", e)
            ok = False
        if not ok:
            log.warning("Canal público: Telegram recusou (%s)", getattr(r, "text", "")[:200])
            break
        with _lock:
            con.execute("INSERT OR REPLACE INTO canal_posts VALUES (?,?,?,?)",
                        (chave, o.preco, agora.isoformat(timespec="seconds"), o.titulo[:200]))
            con.commit()
        postados += 1
    if postados and enviar and cfg["chat"] and token:
        with _lock:
            con.execute("INSERT OR REPLACE INTO canal_posts VALUES (?,?,?,?)",
                        (marca, 0, agora.isoformat(timespec="seconds"), "horário usado"))
            con.commit()
    log.info("Canal público: %d rascunho(s) no horário %s.", postados, slot)
    return postados
