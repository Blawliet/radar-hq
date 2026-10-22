"""Canais de envio dos alertas: Telegram (padrão), Evolution API, Baileys e Meta Cloud API.

Todos expõem a mesma interface: enviar_alerta(destino, campos, texto) -> bool
"""
from __future__ import annotations

import logging
import os
import re
import time

import httpx

log = logging.getLogger("whatsapp")


class EvolutionClient:
    """Cliente HTTP da Evolution API v2."""

    def __init__(self, base_url: str, api_key: str, instancia: str, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self.instancia = instancia
        self.http = httpx.Client(
            timeout=timeout,
            headers={"apikey": api_key, "Content-Type": "application/json"},
        )

    # ---------- gestão da instância ----------
    def criar_instancia(self) -> dict:
        r = self.http.post(
            f"{self.base}/instance/create",
            json={
                "instanceName": self.instancia,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS",
            },
        )
        r.raise_for_status()
        return r.json()

    def qrcode(self) -> dict:
        """Retorna {'base64': 'data:image/png;base64,...', 'code': '...'}"""
        r = self.http.get(f"{self.base}/instance/connect/{self.instancia}")
        r.raise_for_status()
        return r.json()

    def estado(self) -> str:
        r = self.http.get(f"{self.base}/instance/connectionState/{self.instancia}")
        r.raise_for_status()
        return r.json().get("instance", {}).get("state", "unknown")

    def configurar_webhook(self, url: str) -> dict:
        r = self.http.post(
            f"{self.base}/webhook/set/{self.instancia}",
            json={
                "webhook": {
                    "enabled": True,
                    "url": url,
                    "byEvents": False,
                    "base64": False,
                    "events": ["MESSAGES_UPSERT"],
                }
            },
        )
        r.raise_for_status()
        return r.json()

    # ---------- envio ----------
    def enviar_texto(self, numero: str, texto: str, tentativas: int = 3) -> bool:
        payload = {"number": numero, "text": texto, "linkPreview": True}
        for i in range(1, tentativas + 1):
            try:
                r = self.http.post(
                    f"{self.base}/message/sendText/{self.instancia}", json=payload
                )
                if r.status_code < 300:
                    return True
                log.warning("Evolution respondeu %s: %s", r.status_code, r.text[:300])
            except httpx.HTTPError as e:
                log.warning("Falha de rede ao enviar (tentativa %s): %s", i, e)
            time.sleep(3 * i)
        return False


class BaileysClient:
    """Cliente do microserviço Node em whatsapp-baileys/ (POST /send)."""

    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout, headers={"x-token": token})

    def enviar_texto(self, numero: str, texto: str, tentativas: int = 3) -> bool:
        for i in range(1, tentativas + 1):
            try:
                r = self.http.post(f"{self.base}/send", json={"number": numero, "text": texto})
                if r.status_code < 300:
                    return True
                log.warning("Baileys respondeu %s: %s", r.status_code, r.text[:300])
            except httpx.HTTPError as e:
                log.warning("Falha de rede ao enviar (tentativa %s): %s", i, e)
            time.sleep(3 * i)
        return False


class MetaCloudClient:
    """API oficial da Meta usando o NÚMERO DE TESTE gratuito.

    O número de teste envia mensagens grátis para até 5 destinatários verificados.
    Fora da janela de 24h (aberta quando VOCÊ manda mensagem para o número de teste),
    só templates são aceitos; por isso o modo padrão é template.
    """

    def __init__(self, phone_number_id: str, token: str, template: str = "alerta_oferta",
                 idioma: str = "pt_BR", versao: str = "v25.0", modo: str = "template"):
        self.url = f"https://graph.facebook.com/{versao}/{phone_number_id}/messages"
        self.template, self.idioma, self.modo = template, idioma, modo
        self.http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"})

    def _post(self, payload: dict) -> bool:
        for i in range(1, 4):
            try:
                r = self.http.post(self.url, json=payload)
                if r.status_code < 300:
                    return True
                erro = r.json().get("error", {}) if "json" in r.headers.get("content-type", "") else {}
                log.warning("Meta respondeu %s: %s", r.status_code, erro.get("message") or r.text[:300])
                if erro.get("code") == 131047:
                    log.warning("Fora da janela de 24h: texto livre bloqueado. Use META_MODO=template.")
                    return False
                if r.status_code in (400, 401, 403):
                    return False      # erro de configuração, não adianta repetir
            except httpx.HTTPError as e:
                log.warning("Falha de rede ao enviar (tentativa %s): %s", i, e)
            time.sleep(3 * i)
        return False

    def payload_template(self, numero: str, valores: list[str]) -> dict:
        return {
            "messaging_product": "whatsapp", "to": numero, "type": "template",
            "template": {
                "name": self.template, "language": {"code": self.idioma},
                "components": [{"type": "body",
                                "parameters": [{"type": "text", "text": v} for v in valores]}],
            },
        }

    def enviar_texto(self, numero: str, texto: str) -> bool:
        return self._post({"messaging_product": "whatsapp", "to": numero, "type": "text",
                           "text": {"body": texto, "preview_url": True}})

    def enviar_alerta(self, numero: str, valores: list[str], texto: str) -> bool:
        if self.modo == "texto":
            return self.enviar_texto(numero, texto)
        return self._post(self.payload_template(numero, valores))


def _alerta_por_texto(self, numero: str, valores: list[str], texto: str) -> bool:
    return self.enviar_texto(numero, texto)


EvolutionClient.enviar_alerta = _alerta_por_texto
BaileysClient.enviar_alerta = _alerta_por_texto


class TelegramClient:
    """Bot do Telegram (API oficial, grátis). Converte o *negrito* do WhatsApp para HTML
    e adiciona um botão "Comprar" com o link do produto."""

    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.http = httpx.Client(timeout=30)

    @staticmethod
    def para_html(texto: str) -> str:
        texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", texto)

    def payload(self, texto: str, url_botao: str | None = None) -> dict:
        p = {"chat_id": self.chat_id, "text": self.para_html(texto), "parse_mode": "HTML",
             "link_preview_options": {"is_disabled": False}}
        if url_botao and url_botao.startswith("http"):
            p["reply_markup"] = {"inline_keyboard": [[{"text": "🛒 Comprar agora", "url": url_botao}]]}
        return p

    def _post(self, payload: dict) -> bool:
        for i in range(1, 4):
            try:
                r = self.http.post(self.url, json=payload)
                if r.status_code == 200:
                    return True
                dados = r.json() if "json" in r.headers.get("content-type", "") else {}
                log.warning("Telegram respondeu %s: %s", r.status_code, dados.get("description") or r.text[:300])
                if r.status_code == 429:
                    time.sleep(int(dados.get("parameters", {}).get("retry_after", 5)))
                    continue
                if r.status_code in (400, 401, 403, 404):
                    return False     # token, chat_id ou bot bloqueado: não adianta repetir
            except httpx.HTTPError as e:
                log.warning("Falha de rede ao enviar (tentativa %s): %s", i, e)
            time.sleep(3 * i)
        return False

    def enviar_texto(self, numero: str, texto: str) -> bool:
        return self._post(self.payload(texto))

    def enviar_alerta(self, numero: str, valores: list[str], texto: str) -> bool:
        return self._post(self.payload(texto, valores[-1] if valores else None))


def cliente_do_ambiente():
    backend = (os.getenv("CANAL") or os.getenv("WHATSAPP_BACKEND") or "telegram").lower()
    if backend == "telegram":
        return TelegramClient(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    if backend == "meta":
        return MetaCloudClient(
            os.environ["META_PHONE_NUMBER_ID"],
            os.environ["META_TOKEN"],
            os.getenv("META_TEMPLATE", "alerta_oferta"),
            os.getenv("META_IDIOMA", "pt_BR"),
            os.getenv("META_API_VERSION", "v25.0"),
            os.getenv("META_MODO", "template"),
        )
    if backend == "baileys":
        return BaileysClient(os.environ["BAILEYS_URL"], os.environ["BAILEYS_TOKEN"])
    return EvolutionClient(
        os.getenv("EVOLUTION_URL", "http://localhost:8080"),
        os.environ["EVOLUTION_API_KEY"],
        os.getenv("EVOLUTION_INSTANCE", "radar"),
    )
