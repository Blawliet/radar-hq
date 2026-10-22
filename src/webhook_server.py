"""Escuta mensagens de grupos de ofertas (via webhook da Evolution API) e
encaminha só as que batem com suas palavras-chave e passam no catálogo.

Ativação: WEBHOOK_ENABLED=true no .env. O monitor registra o webhook na
Evolution automaticamente ao subir.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from models import Oferta
from rules import Catalogo, avaliar
from stock import validar_html
from stores.base import Fetcher
from texto import contem, normalizar

log = logging.getLogger("webhook")
RAIZ = Path(__file__).resolve().parent.parent
URL_RX = re.compile(r"https?://\S+")


def _texto_da_mensagem(data: dict) -> str:
    msg = data.get("message") or {}
    return (msg.get("conversation")
            or (msg.get("extendedTextMessage") or {}).get("text")
            or (msg.get("imageMessage") or {}).get("caption")
            or (msg.get("videoMessage") or {}).get("caption")
            or "")


class Processador:
    def __init__(self):
        from whatsapp import cliente_do_ambiente
        self.cli = cliente_do_ambiente()
        self.destino = os.environ["WHATSAPP_DESTINO"]
        self.palavras = [p.strip() for p in os.getenv("WEBHOOK_PALAVRAS", "").split(",") if p.strip()]
        self.so_validados = os.getenv("WEBHOOK_SO_VALIDADOS", "true").lower() == "true"
        self.cat = Catalogo(RAIZ / "config" / "catalogo.yaml")
        self.f = Fetcher(delay=1)
        self.vistos: set[str] = set()

    def processar(self, evento: dict):
        dados = evento.get("data")
        for data in (dados if isinstance(dados, list) else [dados]):
            if not isinstance(data, dict):
                continue
            key = data.get("key", {})
            if key.get("fromMe") or not str(key.get("remoteJid", "")).endswith("@g.us"):
                continue
            texto = _texto_da_mensagem(data)
            t = normalizar(texto)
            if not texto or not any(contem(t, p) for p in self.palavras):
                continue
            urls = URL_RX.findall(texto)
            if not urls or urls[0] in self.vistos:
                continue
            self.vistos.add(urls[0])

            # bloqueios do catálogo valem aqui também
            dec = avaliar(Oferta(loja="grupo", titulo=texto[:300], url=urls[0], preco=0), self.cat)
            if dec.motivo.startswith("bloqueado"):
                continue

            r = self.f.get(urls[0])
            res = validar_html(urls[0], r.url_final, r.status, r.html, False)
            if self.so_validados and not res.disponivel:
                log.info("Grupo: descartado (%s) %s", res.motivo, r.url_final)
                continue
            selo = "✅ estoque confirmado" if res.disponivel else "⚠️ estoque não confirmado"
            aviso = (f"📣 *OFERTA VISTA EM GRUPO* ({selo})\n\n"
                     f"{texto.strip()[:900]}\n\n🔗 *Link:* {r.url_final or urls[0]}")
            self.cli.enviar_texto(self.destino, aviso)


def iniciar_webhook(porta: int = 8099):
    proc = Processador()

    url_publica = os.getenv("WEBHOOK_URL", f"http://radar:{porta}/webhook")
    if (os.getenv("CANAL") or os.getenv("WHATSAPP_BACKEND")) == "evolution":
        try:
            proc.cli.configurar_webhook(url_publica)
            log.info("Webhook registrado na Evolution: %s", url_publica)
        except Exception as e:
            log.warning("Não consegui registrar o webhook (%s). Configure manualmente.", e)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            tamanho = int(self.headers.get("Content-Length", 0))
            corpo = self.rfile.read(tamanho)
            self.send_response(200)
            self.end_headers()
            try:
                evento = json.loads(corpo or b"{}")
                if str(evento.get("event", "")).lower().replace("_", ".") == "messages.upsert":
                    threading.Thread(target=proc.processar, args=(evento,), daemon=True).start()
            except json.JSONDecodeError:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", porta), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Webhook ouvindo na porta %d", porta)
