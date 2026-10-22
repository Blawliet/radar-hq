"""SQLite: evita alertas repetidos e guarda preços de capa de referência."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path


class Banco:
    def __init__(self, caminho: str | Path):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(caminho, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.con.executescript("""
                CREATE TABLE IF NOT EXISTS alertas (
                    url TEXT PRIMARY KEY, preco REAL, enviado_em TEXT, regra TEXT);
                CREATE TABLE IF NOT EXISTS capas (
                    chave TEXT PRIMARY KEY, preco REAL, loja TEXT, atualizado TEXT);
                CREATE TABLE IF NOT EXISTS precos (
                    chave TEXT, url TEXT, dia TEXT, preco REAL, loja TEXT,
                    PRIMARY KEY (chave, url, dia));
                CREATE TABLE IF NOT EXISTS historico (
                    quando TEXT, loja TEXT, titulo TEXT, url TEXT, preco REAL,
                    disponivel INTEGER, motivo TEXT);
            """)
            # mantém o arquivo pequeno (ele é salvo no repositório a cada janela)
            limite = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
            self.con.execute("DELETE FROM historico WHERE quando < ?", (limite,))
            self.con.commit()

    # ---------- anti-spam
    def deve_alertar(self, url: str, preco: float, realertar_dias: int = 7,
                     queda_minima: float = 0.03) -> bool:
        with self.lock:
            row = self.con.execute("SELECT preco, enviado_em FROM alertas WHERE url=?", (url,)).fetchone()
        if not row:
            return True
        preco_ant, quando = row
        if preco < preco_ant * (1 - queda_minima):
            return True     # preço caiu de novo
        return datetime.fromisoformat(quando) < datetime.now() - timedelta(days=realertar_dias)

    def registrar_alerta(self, url: str, preco: float, regra: str):
        with self.lock, self.con:
            self.con.execute(
                "INSERT INTO alertas VALUES (?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
                "preco=excluded.preco, enviado_em=excluded.enviado_em, regra=excluded.regra",
                (url, preco, datetime.now().isoformat(timespec="seconds"), regra))

    # ---------- preço de capa de referência (lojas oficiais)
    def salvar_capa(self, chave: str, preco: float, loja: str):
        with self.lock, self.con:
            self.con.execute(
                "INSERT INTO capas VALUES (?,?,?,?) ON CONFLICT(chave) DO UPDATE SET "
                "preco=excluded.preco, loja=excluded.loja, atualizado=excluded.atualizado",
                (chave, preco, loja, datetime.now().isoformat(timespec="seconds")))

    def capa(self, chave: str) -> float | None:
        with self.lock:
            row = self.con.execute("SELECT preco FROM capas WHERE chave=?", (chave,)).fetchone()
        return row[0] if row else None

    # ---------- histórico de preços (todas as lojas, só ofertas com estoque confirmado)
    def registrar_preco(self, chave: str, url: str, preco: float, loja: str):
        dia = datetime.now().strftime("%Y-%m-%d")
        with self.lock, self.con:
            self.con.execute(
                "INSERT INTO precos VALUES (?,?,?,?,?) ON CONFLICT(chave, url, dia) DO UPDATE SET "
                "preco=MIN(preco, excluded.preco)", (chave, url, dia, preco, loja))

    def menor_preco_anterior(self, chave: str, url: str) -> tuple[float, str, str] | None:
        """Menor preço já registrado para o produto, sem contar o registro de hoje deste mesmo link.
        Devolve (preco, loja, 'dd/mm/aaaa') ou None se o produto nunca foi visto antes."""
        hoje = datetime.now().strftime("%Y-%m-%d")
        with self.lock:
            row = self.con.execute(
                "SELECT preco, loja, dia FROM precos WHERE chave=? AND NOT (url=? AND dia=?) "
                "ORDER BY preco ASC, dia ASC LIMIT 1", (chave, url, hoje)).fetchone()
        if not row:
            return None
        a, m, d = row[2].split("-")
        return row[0], row[1], f"{d}/{m}/{a}"

    # ---------- log de tudo que foi checado (útil para depurar)
    def historico(self, loja, titulo, url, preco, disponivel, motivo):
        with self.lock, self.con:
            self.con.execute("INSERT INTO historico VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(timespec="seconds"), loja, titulo, url,
                              preco, int(bool(disponivel)), motivo))
