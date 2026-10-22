"""Infra comum dos coletores: HTTP educado, navegador opcional e interface de loja."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from models import Oferta

log = logging.getLogger("coleta")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
]


@dataclass
class Resposta:
    status: int
    url_final: str
    html: str
    via: str = "http"


class Fetcher:
    """GET com pausa por domínio, retentativa e Playwright opcional."""

    def __init__(self, delay: float | None = None):
        self.delay = float(delay if delay is not None else os.getenv("DELAY_SEGUNDOS", "4"))
        self._ultimo: dict[str, float] = {}
        self._lock = threading.Lock()
        self.http = httpx.Client(
            follow_redirects=True,
            timeout=25,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
        )
        self._pw = None
        self._browser = None

    def _esperar(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            falta = self.delay - (time.time() - self._ultimo.get(host, 0))
            if falta > 0:
                time.sleep(falta + random.uniform(0, 1.2))
            self._ultimo[host] = time.time()

    def get(self, url: str, json: bool = False, headers: dict | None = None, tentativas: int = 2):
        for i in range(tentativas):
            self._esperar(url)
            try:
                r = self.http.get(url, headers=headers)
                if r.status_code in (429, 503) and i + 1 < tentativas:
                    time.sleep(10 * (i + 1))
                    continue
                if json:
                    return r.status_code, (r.json() if r.status_code == 200 else None)
                return Resposta(r.status_code, str(r.url), r.text)
            except (httpx.HTTPError, ValueError) as e:
                log.debug("GET %s falhou: %s", url, e)
                time.sleep(3)
        return (0, None) if json else Resposta(0, url, "")

    # ---------- navegador real (para lojas com JavaScript pesado) ----------
    def get_render(self, url: str, esperar_seletor: str | None = None) -> Resposta:
        if os.getenv("USE_PLAYWRIGHT", "true").lower() != "true":
            return self.get(url)
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            self._esperar(url)
            ctx = self._browser.new_context(
                user_agent=random.choice(USER_AGENTS), locale="pt-BR",
                viewport={"width": 1366, "height": 900},
            )
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if esperar_seletor:
                try:
                    page.wait_for_selector(esperar_seletor, timeout=8000)
                except Exception:
                    pass
            page.wait_for_timeout(1500)
            out = Resposta(resp.status if resp else 0, page.url, page.content(), via="browser")
            ctx.close()
            return out
        except Exception as e:
            log.warning("Playwright falhou em %s (%s), tentando HTTP simples.", url, e)
            return self.get(url)

    def fechar(self):
        if self._browser:
            self._browser.close()
            self._pw.stop()
        self.http.close()


class Loja:
    """Interface: buscar() devolve candidatos; validar() confirma estoque na página."""

    nome: str = "Loja"
    oficial: bool = False      # loja da editora: preço cheio vira referência de capa

    def __init__(self, fetcher: Fetcher, cfg: dict):
        self.f = fetcher
        self.cfg = cfg
        self.nome = cfg.get("nome", self.nome)
        self.oficial = cfg.get("oficial", False)
        self.max_resultados = int(cfg.get("max_resultados", 12))

    def buscar(self, termo: str) -> list[Oferta]:
        raise NotImplementedError

    def validar(self, oferta: Oferta) -> Oferta:
        raise NotImplementedError
