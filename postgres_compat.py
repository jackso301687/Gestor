"""Compatibilidade mínima entre a API sqlite3 usada pelo ERP e psycopg.

O objetivo é preservar as regras de negócio existentes enquanto o banco de
produção usa PostgreSQL. A tradução fica concentrada aqui e não altera
produção, medição ou pagamento.
"""
from __future__ import annotations

import re
import sqlite3


IDENTITY_TABLES = {
    "ajustes_pagamento", "apontamento_vinculos", "apropriacoes", "auditoria",
    "avaliacoes_qualidade", "descontos_pagamento", "descontos_servico",
    "extras_pagamento", "fechamentos_pagamento", "funcionario_servico_precos",
    "funcionarios", "importacao_inconsistencias", "importacoes", "itens_pagamento",
    "lancamentos", "medicoes_pms", "obras", "pagamentos", "previsao_pms",
    "proposta_itens", "proposta_pagamento_itens", "proposta_servicos",
    "propostas_medicao", "propostas_pagamento", "qualidade_historico",
    "reembolso_justificativas", "reembolsos", "servico_precos", "servicos",
    "sessoes", "tentativas_login", "usuarios",
}


class CompatRow:
    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(values)
        self._data = dict(zip(self._names, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._names)

    def keys(self):
        return self._names


def compat_row(cursor):
    names = [column.name for column in cursor.description]
    return lambda values: CompatRow(names, values)


def _replace_qmarks(sql: str) -> str:
    result = []
    quoted = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quoted:
            result.append(char)
            if char == quoted:
                if index + 1 < len(sql) and sql[index + 1] == quoted:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quoted = None
        elif char in ("'", '"'):
            quoted = char
            result.append(char)
        elif char == "?":
            result.append("%s")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _cast_rounded_numbers(sql: str) -> str:
    """PostgreSQL exige numeric em ROUND(valor, casas); SQLite aceita REAL."""
    upper = sql.upper()
    output = []
    cursor = 0
    while True:
        start = upper.find("ROUND(", cursor)
        if start < 0:
            output.append(sql[cursor:])
            break
        output.append(sql[cursor:start])
        depth = 1
        quoted = None
        comma = None
        end = start + 6
        while end < len(sql) and depth:
            char = sql[end]
            if quoted:
                if char == quoted:
                    quoted = None
            elif char in ("'", '"'):
                quoted = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 1:
                comma = end
            end += 1
        if depth or comma is None:
            output.append(sql[start:end])
            cursor = end
            continue
        expression = sql[start + 6:comma].strip()
        precision = sql[comma + 1:end - 1].strip()
        if precision.isdigit() and not re.match(r"^CAST\(.+\s+AS\s+numeric\)$", expression, re.I | re.S):
            output.append(f"ROUND(CAST({expression} AS numeric), {precision})")
        else:
            output.append(sql[start:end])
        cursor = end
    return "".join(output)


def translate_sql(sql: str) -> tuple[str, str | None]:
    statement = sql.strip().rstrip(";")
    ignored = bool(re.match(r"INSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.I))
    statement = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
    statement = re.sub(
        r"(\b[\w.]+)\s*=\s*\?\s+COLLATE\s+NOCASE\b",
        r"LOWER(\1)=LOWER(?)",
        statement,
        flags=re.I,
    )
    statement = re.sub(
        r"datetime\(\s*'now'\s*,\s*'-15 minutes'\s*\)",
        "(CURRENT_TIMESTAMP - INTERVAL '15 minutes')",
        statement,
        flags=re.I,
    )
    statement = re.sub(r"julianday\(([^)]+)\)", r"CAST(\1 AS timestamptz)", statement, flags=re.I)
    statement = re.sub(r"\bdate\(([^)]+)\)", r"CAST(\1 AS date)", statement, flags=re.I)
    statement = re.sub(
        r"GROUP_CONCAT\(DISTINCT\s+([^)]+)\)",
        r"STRING_AGG(DISTINCT CAST(\1 AS text), ',')",
        statement,
        flags=re.I,
    )
    statement = _cast_rounded_numbers(statement)
    if ignored and not re.search(r"\bON\s+CONFLICT\b", statement, re.I):
        statement += " ON CONFLICT DO NOTHING"
    table_match = re.match(r"INSERT\s+INTO\s+[`\"]?([A-Za-z_]\w*)", statement, re.I)
    table = table_match.group(1).lower() if table_match else None
    wants_id = table in IDENTITY_TABLES and not re.search(r"\bRETURNING\b", statement, re.I)
    if wants_id:
        statement += " RETURNING id"
    return _replace_qmarks(statement), table if wants_id else None


class Cursor:
    def __init__(self, raw, lastrowid=None):
        self._raw = raw
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        return self._raw.rowcount

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    def __iter__(self):
        return iter(self._raw)


class Connection:
    dialect = "postgresql"

    def __init__(self, database_url: str):
        import psycopg
        self._psycopg = psycopg
        self._raw = psycopg.connect(database_url, row_factory=compat_row)

    def execute(self, sql, params=()):
        translated, returning_id = translate_sql(sql)
        cursor = self._raw.cursor()
        try:
            cursor.execute(translated, tuple(params or ()))
            lastrowid = None
            if returning_id:
                row = cursor.fetchone()
                lastrowid = row[0] if row else None
            return Cursor(cursor, lastrowid)
        except self._psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def executemany(self, sql, rows):
        translated, _ = translate_sql(sql)
        translated = re.sub(r"\s+RETURNING\s+id\s*$", "", translated, flags=re.I)
        cursor = self._raw.cursor()
        try:
            cursor.executemany(translated, rows)
            return Cursor(cursor)
        except self._psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False


def connect(database_url: str) -> Connection:
    return Connection(database_url)
