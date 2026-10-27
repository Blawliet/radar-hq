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
            # colunas novas da tabela de alertas (para o resumo do dia)
            existentes = {r[1] for r in self.con.execute("PRAGMA table_info(alertas)")}
            for col, tipo in (("titulo", "TEXT"), ("loja", "TEXT"), ("desconto", "REAL"),
                              ("pessoas", "TEXT"), ("recorde", "INTEGER")):
                if col not in existentes:
                    self.con.execute(f"ALTER TABLE alertas ADD COLUMN {col} {tipo}")
            self.con.execute("""CREATE TABLE IF NOT EXISTS log_alertas (
                quando TEXT, url TEXT, titulo TEXT, loja TEXT, preco REAL, regra TEXT,
                desconto REAL, pessoas TEXT, recorde INTEGER)""")
            self.con.execute("""CREATE TABLE IF NOT EXISTS diagnostico (
                quando TEXT, loja TEXT, buscas INTEGER, resultados INTEGER, erros INTEGER,
                validadas INTEGER, com_estoque INTEGER, segundos REAL)""")
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

    def registrar_alerta(self, url: str, preco: float, regra: str, titulo: str | None = None,
                         loja: str | None = None, desconto: float | None = None,
                         pessoas: str | None = None, recorde: bool = False):
        agora = datetime.now().isoformat(timespec="seconds")
        with self.lock, self.con:
            self.con.execute(
                "INSERT INTO alertas (url, preco, enviado_em, regra, titulo, loja, desconto, pessoas, recorde) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
                "preco=excluded.preco, enviado_em=excluded.enviado_em, regra=excluded.regra, "
                "titulo=excluded.titulo, loja=excluded.loja, desconto=excluded.desconto, "
                "pessoas=excluded.pessoas, recorde=excluded.recorde",
                (url, preco, agora, regra, titulo, loja, desconto, pessoas, int(recorde)))
            self.con.execute("INSERT INTO log_alertas VALUES (?,?,?,?,?,?,?,?,?)",
                             (agora, url, titulo, loja, preco, regra, desconto, pessoas, int(recorde)))

    def alertas_do_dia(self, dia: str) -> list[dict]:
        """dia = 'AAAA-MM-DD'. Cada link aparece uma vez, com o último preço enviado no dia."""
        with self.lock:
            rows = self.con.execute(
                "SELECT l.url, l.titulo, l.loja, l.preco, l.regra, l.desconto, l.pessoas, "
                "(SELECT MAX(recorde) FROM log_alertas r WHERE r.url=l.url AND substr(r.quando,1,10)=?), l.quando "
                "FROM log_alertas l WHERE l.rowid IN (SELECT MAX(rowid) FROM log_alertas "
                "WHERE substr(quando,1,10)=? GROUP BY url)", (dia, dia)).fetchall()
        campos = ("url", "titulo", "loja", "preco", "regra", "desconto", "pessoas", "recorde", "quando")
        return [dict(zip(campos, r)) for r in rows]

    def checagens_do_dia(self, dia: str) -> tuple[int, int]:
        with self.lock:
            r = self.con.execute("SELECT COUNT(*), COUNT(DISTINCT loja) FROM historico "
                                 "WHERE substr(quando,1,10)=?", (dia,)).fetchone()
        return r[0], r[1]

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

    def menor_preco_anterior(self, chave: str, url: str,
                             outras_urls: list[str] | tuple = ()) -> tuple[float, str, str] | None:
        """Menor preço já registrado para o produto, sem contar os registros de hoje dos links
        desta varredura (eles já aparecem em "Também em"). Devolve (preco, loja, 'dd/mm/aaaa')
        ou None se o produto nunca foi visto antes."""
        hoje = datetime.now().strftime("%Y-%m-%d")
        urls = [url, *outras_urls]
        marcas = ",".join("?" * len(urls))
        with self.lock:
            row = self.con.execute(
                f"SELECT preco, loja, dia FROM precos WHERE chave=? AND NOT (dia=? AND url IN ({marcas})) "
                "ORDER BY preco ASC, dia ASC LIMIT 1", (chave, hoje, *urls)).fetchone()
        if not row:
            return None
        a, m, d = row[2].split("-")
        return row[0], row[1], f"{d}/{m}/{a}"

    def preco_tipico_volume(self, serie: str) -> float | None:
        """Preço típico de UM volume avulso da série: mediana das capas oficiais;
        sem capas, mediana dos preços vistos (precisa de pelo menos 3)."""
        import statistics
        s = serie.strip()
        with self.lock:
            capas = [r[0] for r in self.con.execute(
                "SELECT preco FROM capas WHERE (chave LIKE ? OR chave LIKE ? OR chave LIKE ?) "
                "AND chave NOT LIKE '%unico' AND chave NOT LIKE '%pacote%'",
                (f"{s}:%", f"capa:{s}:%", f"capa:titulo:{s} %"))]
            vistos = [r[0] for r in self.con.execute(
                "SELECT preco FROM precos WHERE (chave LIKE ? OR chave LIKE ?) "
                "AND chave NOT LIKE '%unico' AND chave NOT LIKE '%pacote%'",
                (f"{s}:%", f"titulo:{s} %"))]
        if capas:
            return round(statistics.median(capas), 2)
        if len(vistos) >= 3:
            return round(statistics.median(vistos), 2)
        return None

    def diagnostico(self, loja, buscas, resultados, erros, validadas, com_estoque, segundos):
        with self.lock, self.con:
            self.con.execute("INSERT INTO diagnostico VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(timespec="seconds"), loja, buscas, resultados,
                              erros, validadas, com_estoque, round(segundos, 1)))

    def saude_lojas(self, dia: str) -> dict[str, dict]:
        """Números do dia por loja, para o boletim do resumão. dia = 'AAAA-MM-DD'."""
        from datetime import date, timedelta as td
        semana = (date.fromisoformat(dia) - td(days=6)).isoformat()
        out: dict[str, dict] = {}
        with self.lock:
            for loja, runs, buscas, res, erros, valid, estoque, seg in self.con.execute(
                    "SELECT loja, COUNT(*), SUM(buscas), SUM(resultados), SUM(erros), SUM(validadas), "
                    "SUM(com_estoque), AVG(segundos) FROM diagnostico WHERE substr(quando,1,10)=? "
                    "GROUP BY loja", (dia,)):
                out[loja] = dict(varreduras=runs, buscas=buscas, resultados=res, erros=erros,
                                 conferidas=valid, com_estoque=estoque, segundos=seg or 0)
            for loja, total, ok, bloq in self.con.execute(
                    "SELECT loja, COUNT(*), SUM(disponivel), SUM(CASE WHEN lower(motivo) LIKE '%bloque%' "
                    "OR lower(motivo) LIKE '%anti-rob%' THEN 1 ELSE 0 END) FROM historico "
                    "WHERE substr(quando,1,10)=? GROUP BY loja", (dia,)):
                o = out.setdefault(loja, {})
                o.setdefault("conferidas", total)
                o.setdefault("com_estoque", ok)
                o["bloqueadas"] = bloq or 0
                motivo = self.con.execute(
                    "SELECT motivo, COUNT(*) FROM historico WHERE loja=? AND disponivel=0 "
                    "AND substr(quando,1,10)=? GROUP BY motivo ORDER BY 2 DESC LIMIT 1", (loja, dia)).fetchone()
                o["motivo_principal"] = motivo[0] if motivo else None
            for loja, n in self.con.execute(
                    "SELECT loja, COUNT(DISTINCT url) FROM log_alertas WHERE substr(quando,1,10)=? GROUP BY loja",
                    (dia,)):
                out.setdefault(loja, {})["alertas"] = n
            for loja, n in self.con.execute(
                    "SELECT loja, COUNT(DISTINCT url) FROM log_alertas WHERE substr(quando,1,10) BETWEEN ? AND ? "
                    "GROUP BY loja", (semana, dia)):
                out.setdefault(loja, {})["alertas_7d"] = n
        return out

    # ---------- log de tudo que foi checado (útil para depurar)
    def historico(self, loja, titulo, url, preco, disponivel, motivo):
        with self.lock, self.con:
            self.con.execute("INSERT INTO historico VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(timespec="seconds"), loja, titulo, url,
                              preco, int(bool(disponivel)), motivo))
