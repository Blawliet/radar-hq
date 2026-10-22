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

    # ---------- log de tudo que foi checado (útil para depurar)
    def historico(self, loja, titulo, url, preco, disponivel, motivo):
        with self.lock, self.con:
            self.con.execute("INSERT INTO historico VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(timespec="seconds"), loja, titulo, url,
                              preco, int(bool(disponivel)), motivo))
