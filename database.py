"""Persistência e regras de negócio do PMS usando apenas a biblioteca padrão."""
from __future__ import annotations

import json
import hashlib
import hmac
import calendar
import math
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from migrations import MIGRATIONS, v0004_application_settings
from apontamento_store import import_sqlite_source
from name_normalization import normalize_current_names

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PMS_DB", ROOT / "pms.sqlite3"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SOURCE_SQL = ROOT / "schema_e_dados_obra.sql"
PASSWORD_ITERATIONS = 310_000
SESSION_HOURS = 8
REFERENCE_DB = ROOT / "pms.sqlite3"
BACKUP_REFERENCE_DB = ROOT / "pms-before-master-20260919.sqlite3"
APONTAMENTO_SOURCE_DB = ROOT / "data" / "apontamento.sqlite3"

PROFILES = ("admin", "engenharia", "qualidade", "financeiro", "operador")
PERMISSIONS = {
    "admin": {"*"},
    "engenharia": {"consultar", "lancamentos", "apropriacoes", "pms", "relatorios"},
    "qualidade": {"consultar", "qualidade", "pms", "relatorios"},
    "financeiro": {"consultar", "pagamentos", "ajustes", "relatorios", "exportar"},
    "operador": {"consultar", "lancamentos", "apropriacoes"},
}


def using_postgres() -> bool:
    return bool(DATABASE_URL)


def connect():
    if using_postgres():
        from postgres_compat import connect as postgres_connect
        return postgres_connect(DATABASE_URL)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _sql_statements(text: str):
    buffer = []
    quoted = False
    escaped = False
    for char in text:
        buffer.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == "'":
            quoted = not quoted
        elif char == ";" and not quoted:
            yield "".join(buffer)
            buffer = []


def _split_values(block: str):
    tuples, current = [], []
    quoted = escaped = False
    depth = 0
    for char in block:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            current.append(char)
            escaped = True
            continue
        if char == "'":
            quoted = not quoted
            current.append(char)
            continue
        if not quoted and char == "(":
            depth += 1
            if depth == 1:
                current = []
                continue
        if not quoted and char == ")":
            depth -= 1
            if depth == 0:
                tuples.append(_parse_tuple("".join(current)))
                current = []
                continue
        if depth:
            current.append(char)
    return tuples


def _parse_tuple(raw: str):
    values, current = [], []
    quoted = escaped = False
    for char in raw + ",":
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == "'":
            quoted = not quoted
        elif char == "," and not quoted:
            token = "".join(current).strip()
            if token.upper() == "NULL" or token == "":
                values.append(None)
            else:
                try:
                    values.append(float(token) if "." in token else int(token))
                except ValueError:
                    values.append(token.replace("''", "'"))
            current = []
        else:
            current.append(char)
    return values


def import_source_data(connection: sqlite3.Connection) -> dict:
    allowed = {
        "obras", "funcionarios", "servicos", "lancamentos",
        "previsao_pms", "descontos_servico", "reembolso_justificativas"
    }
    imported = {name: 0 for name in allowed}
    source = SOURCE_SQL.read_text(encoding="utf-8")
    text = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("--"))
    for statement in _sql_statements(text):
        match = re.search(
            r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*(.*);",
            statement, re.IGNORECASE | re.DOTALL,
        )
        if not match or match.group(1) not in allowed:
            continue
        table = match.group(1)
        columns = [part.strip().strip("`") for part in match.group(2).split(",")]
        rows = _split_values(match.group(3))
        if table == "lancamentos":
            discount_index = columns.index("desconto")
            extra_index = columns.index("extra")
            type_index = columns.index("tipo")
            for row in rows:
                row[discount_index] = row[discount_index] or 0
                row[extra_index] = row[extra_index] or 0
                row[type_index] = row[type_index] or "não informado"
        placeholders = ",".join("?" for _ in columns)
        sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        connection.executemany(sql, rows)
        imported[table] += len(rows)
    connection.execute(
        "INSERT INTO auditoria(entidade, acao, detalhes) VALUES(?,?,?)",
        ("sistema", "importacao_inicial", json.dumps(imported, ensure_ascii=False)),
    )
    connection.commit()
    return imported


def init_db(force: bool = False) -> dict:
    if using_postgres():
        with connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='obras'").fetchone()
            if not row or not row[0]:
                raise RuntimeError("O PostgreSQL ainda não foi inicializado. Execute scripts/migrate_sqlite_to_postgres.py.")
        return {"database": "postgresql", "imported": {}}
    if force and DB_PATH.exists():
        DB_PATH.unlink()
    with connect() as connection:
        connection.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        migrate_schema(connection)
        existing = connection.execute("SELECT COUNT(*) FROM obras").fetchone()[0]
        if existing == 0:
            if SOURCE_SQL.exists():
                imported = import_source_data(connection)
            else:
                reference = None
                if REFERENCE_DB.exists() and REFERENCE_DB.resolve() != DB_PATH.resolve():
                    reference = REFERENCE_DB
                elif BACKUP_REFERENCE_DB.exists() and BACKUP_REFERENCE_DB.resolve() != DB_PATH.resolve():
                    reference = BACKUP_REFERENCE_DB
                if reference is None:
                    raise RuntimeError(
                        "Nenhuma fonte de dados encontrada para inicializar o banco. "
                        "Disponibilize schema_e_dados_obra.sql ou uma base de referência."
                    )
                imported = clone_reference_data(connection, reference)
        else:
            imported = {}
        ensure_service_price_history(connection)
        seed_users(connection)
        ensure_user_work_assignments(connection)
        source_work = connection.execute(
            "SELECT id FROM obras WHERE nome='ATLANTA' ORDER BY id LIMIT 1"
        ).fetchone() or connection.execute("SELECT id FROM obras ORDER BY id LIMIT 1").fetchone()
        if source_work:
            import_sqlite_source(connection, APONTAMENTO_SOURCE_DB, source_work["id"])
        connection.execute("INSERT OR IGNORE INTO configuracoes_obra(obra_id) SELECT id FROM obras")
        v0004_application_settings.backfill_legacy_settings(connection)
        normalize_current_names(connection)
        connection.commit()
        if not connection.execute("SELECT 1 FROM medicoes_pms LIMIT 1").fetchone():
            obra = connection.execute("SELECT id, pms_atual FROM obras WHERE nome='ATLANTA'").fetchone()
            if obra:
                fim = connection.execute("SELECT MAX(data) FROM lancamentos WHERE obra_id=?", (obra["id"],)).fetchone()[0]
                fim = date.fromisoformat(fim) if fim else date.today()
                inicio = fim - timedelta(days=14)
                totals = dashboard(connection, obra["id"], inicio.isoformat(), fim.isoformat())
                desconto = connection.execute("SELECT COALESCE(SUM(valor_desconto),0) FROM descontos_servico WHERE obra_id=?", (obra["id"],)).fetchone()[0]
                connection.execute(
                    "INSERT OR IGNORE INTO medicoes_pms(obra_id,numero,periodo_inicio,periodo_fim,status,valor_bruto,valor_desconto,valor_liquido) VALUES(?,?,?,?,?,?,?,?)",
                    (obra["id"], obra["pms_atual"] or 1, inicio.isoformat(), fim.isoformat(), "em_conferencia", totals["total_receber"], desconto, totals["total_receber"] - desconto),
                )
                connection.commit()
    return {"database": str(DB_PATH), "imported": imported}


def migrate_schema(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.VERSION in applied:
            continue
        try:
            with connection:
                migration.upgrade(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version,name) VALUES(?,?)",
                    (migration.VERSION, migration.NAME),
                )
        except sqlite3.Error as error:
            raise RuntimeError(
                f"Falha ao aplicar migration {migration.VERSION} ({migration.NAME}): {error}"
            ) from error


def clone_reference_data(connection, reference_db=None):
    reference_db = Path(reference_db or REFERENCE_DB)
    if reference_db.resolve() == DB_PATH.resolve():
        raise RuntimeError("A base de referência não pode ser a mesma base de destino")
    tables = [
        "obras", "funcionarios", "servicos", "lancamentos",
        "previsao_pms", "descontos_servico", "reembolso_justificativas",
    ]
    imported = {}
    connection.execute("ATTACH DATABASE ? AS seed", (str(reference_db),))
    try:
        for table in tables:
            target_columns = [r["name"] for r in connection.execute(f"PRAGMA table_info({table})")]
            source_columns = {r["name"] for r in connection.execute(f"PRAGMA seed.table_info({table})")}
            columns = [name for name in target_columns if name in source_columns]
            joined = ",".join(columns)
            connection.execute(f"INSERT OR IGNORE INTO {table} ({joined}) SELECT {joined} FROM seed.{table}")
            imported[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        connection.execute(
            "INSERT INTO auditoria(entidade,acao,detalhes) VALUES(?,?,?)",
            ("sistema", "copia_base_referencia", json.dumps(imported, ensure_ascii=False)),
        )
        connection.commit()
    finally:
        connection.execute("DETACH DATABASE seed")
    return imported


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def validate_password(password: str):
    errors = []
    if len(password or "") < 8: errors.append("8 caracteres")
    if not re.search(r"[A-Z]", password or ""): errors.append("uma letra maiúscula")
    if not re.search(r"[a-z]", password or ""): errors.append("uma letra minúscula")
    if not re.search(r"\d", password or ""): errors.append("um número")
    if not re.search(r"[^A-Za-z0-9]", password or ""): errors.append("um símbolo")
    if errors:
        raise ValueError("A senha deve conter " + ", ".join(errors))


def seed_users(connection: sqlite3.Connection):
    if connection.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
        connection.execute(
            """UPDATE usuarios SET perfil=CASE role WHEN 'admin' THEN 'admin' ELSE 'operador' END
               WHERE perfil IS NULL"""
        )
        connection.commit()
        return
    admin_password = os.environ.get("PMS_ADMIN_PASSWORD", "Admin@123")
    user_password = os.environ.get("PMS_USER_PASSWORD", "Usuario@123")
    connection.executemany(
        "INSERT INTO usuarios(nome,username,password_hash,role,perfil,deve_trocar_senha) VALUES(?,?,?,?,?,1)",
        [
            ("Administrador", "admin", hash_password(admin_password), "admin", "admin"),
            ("Usuário operacional", "usuario", hash_password(user_password), "usuario", "operador"),
        ],
    )
    connection.commit()


def ensure_user_work_assignments(connection: sqlite3.Connection):
    """Preserva o acesso legado tornando explícitas as obras dos usuários existentes."""
    connection.execute(
        """INSERT OR IGNORE INTO usuario_obras(usuario_id,obra_id)
           SELECT u.id,o.id FROM usuarios u CROSS JOIN obras o
           WHERE COALESCE(u.perfil, CASE u.role WHEN 'admin' THEN 'admin' ELSE 'operador' END) <> 'admin'
             AND NOT EXISTS (SELECT 1 FROM usuario_obras x WHERE x.usuario_id=u.id)"""
    )
    connection.commit()


def ensure_service_price_history(connection: sqlite3.Connection):
    """Cria a vigência-base dos serviços legados sem alterar lançamentos históricos."""
    connection.execute(
        """INSERT OR IGNORE INTO servico_precos
           (servico_id,obra_id,preco_pagamento,valor_receber_unitario,inicio_vigencia,motivo)
           SELECT s.id,s.obra_id,s.preco_pagamento,s.valor_receber_unitario,
                  COALESCE((SELECT MIN(l.data) FROM lancamentos l WHERE l.servico_id=s.id),'1900-01-01'),
                  'Importação do preço legado'
           FROM servicos s
           WHERE NOT EXISTS (SELECT 1 FROM servico_precos p WHERE p.servico_id=s.id)"""
    )
    connection.commit()


def authenticate(connection: sqlite3.Connection, username: str, password: str, ip: str | None = None):
    username = (username or "").strip().lower()
    ip = ip or "local"
    failures = connection.execute(
        "SELECT COUNT(*) FROM tentativas_login WHERE username=? AND ip=? AND sucesso=0 AND criado_em >= datetime('now','-15 minutes')",
        (username, ip),
    ).fetchone()[0]
    if failures >= 5:
        raise PermissionError("Muitas tentativas. Aguarde 15 minutos para tentar novamente.")
    user = connection.execute("SELECT * FROM usuarios WHERE username=? COLLATE NOCASE", (username,)).fetchone()
    valid = bool(user and user["ativo"] and verify_password(password or "", user["password_hash"]))
    connection.execute("INSERT INTO tentativas_login(username,ip,sucesso) VALUES(?,?,?)", (username, ip, int(valid)))
    if not valid:
        connection.commit()
        raise PermissionError("Usuário ou senha inválidos")
    connection.execute("DELETE FROM tentativas_login WHERE username=? AND ip=?", (username, ip))
    connection.execute("UPDATE usuarios SET ultimo_login=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    csrf_token = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    connection.execute("DELETE FROM sessoes WHERE expira_em <= ?", (datetime.now(timezone.utc).isoformat(),))
    connection.execute("INSERT INTO sessoes(usuario_id,token_hash,csrf_token,ip,expira_em) VALUES(?,?,?,?,?)", (user["id"], token_hash, csrf_token, ip, expires.isoformat()))
    audit(connection, "sessao", None, "login_sucesso", user["id"], ip=ip)
    connection.commit()
    return raw_token, csrf_token, public_user(user)


def public_user(user) -> dict:
    result = {key: user[key] for key in ("id", "nome", "username", "email", "role", "perfil", "ativo", "deve_trocar_senha", "ultimo_login", "criado_em") if key in user.keys()}
    result["perfil"] = result.get("perfil") or ("admin" if result.get("role") == "admin" else "operador")
    result["permissoes"] = sorted(PERMISSIONS.get(result["perfil"], set()))
    return result


def has_permission(user, permission):
    profile = user["perfil"] if "perfil" in user.keys() and user["perfil"] else ("admin" if user["role"] == "admin" else "operador")
    allowed = PERMISSIONS.get(profile, set())
    return "*" in allowed or permission in allowed


def has_work_access(connection, user, obra_id):
    if not user:
        return False
    profile = user["perfil"] if "perfil" in user.keys() and user["perfil"] else (
        "admin" if user["role"] == "admin" else "operador"
    )
    if profile == "admin":
        return True
    return connection.execute(
        "SELECT 1 FROM usuario_obras WHERE usuario_id=? AND obra_id=?",
        (user["id"], int(obra_id)),
    ).fetchone() is not None


def audit(connection, entity, entity_id, action, user_id=None, before=None, after=None, ip=None, details=None):
    connection.execute(
        "INSERT INTO auditoria(entidade,entidade_id,acao,detalhes,usuario_id,antes,depois,ip) VALUES(?,?,?,?,?,?,?,?)",
        (
            entity, entity_id, action,
            json.dumps(details, ensure_ascii=False, default=str) if details is not None else None,
            user_id,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            ip,
        ),
    )


def session_user(connection: sqlite3.Connection, raw_token: str | None):
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    row = connection.execute(
        "SELECT u.*,s.csrf_token,s.id sessao_id,s.expira_em FROM sessoes s JOIN usuarios u ON u.id=s.usuario_id WHERE s.token_hash=? AND s.expira_em>? AND u.ativo=1",
        (token_hash, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    return row


def change_password(connection: sqlite3.Connection, user_id: int, current: str, new_password: str, ip=None):
    user = connection.execute("SELECT * FROM usuarios WHERE id=?", (user_id,)).fetchone()
    if not user or not verify_password(current, user["password_hash"]):
        raise PermissionError("A senha atual está incorreta")
    validate_password(new_password)
    if verify_password(new_password, user["password_hash"]):
        raise ValueError("A nova senha deve ser diferente da atual")
    connection.execute("UPDATE usuarios SET password_hash=?,deve_trocar_senha=0 WHERE id=?", (hash_password(new_password), user_id))
    connection.execute("DELETE FROM sessoes WHERE usuario_id=?", (user_id,))
    audit(
        connection,
        "usuario",
        user_id,
        "senha_alterada",
        user_id,
        before={"deve_trocar_senha": user["deve_trocar_senha"]},
        after={"deve_trocar_senha": 0},
        ip=ip,
    )
    connection.commit()


def calculate_entry(service: sqlite3.Row, quantity, hours, extra=0, discount=0):
    quantity = float(quantity or 0)
    hours = float(hours or 0)
    extra = float(extra or 0)
    discount = float(discount or 0)
    price = float(service["preco_pagamento"] or 0)
    receive_price = float(service["valor_receber_unitario"] or 0)
    standard = float(service["quantidade_padrao"] or 1)
    if quantity > 0:
        base = price * quantity
        receive = receive_price * standard * quantity
    elif hours > 0:
        base = price * hours
        receive = 0
    else:
        base = price
        receive = receive_price * standard
    return round(base + extra - discount, 2), round(receive, 2)


def service_price_at(connection, service_id, effective_date):
    price = connection.execute(
        """SELECT * FROM servico_precos
           WHERE servico_id=? AND ativo=1 AND inicio_vigencia<=?
             AND (fim_vigencia IS NULL OR fim_vigencia>=?)
           ORDER BY inicio_vigencia DESC,id DESC LIMIT 1""",
        (service_id, effective_date, effective_date),
    ).fetchone()
    if price:
        return price
    return connection.execute("SELECT * FROM servicos WHERE id=?", (service_id,)).fetchone()


def employee_service_price_at(connection, employee_id, service_id, effective_date):
    return connection.execute(
        """SELECT * FROM funcionario_servico_precos
           WHERE funcionario_id=? AND servico_id=? AND ativo=1 AND inicio_vigencia<=?
             AND (fim_vigencia IS NULL OR fim_vigencia>=?)
           ORDER BY inicio_vigencia DESC,id DESC LIMIT 1""",
        (employee_id, service_id, effective_date, effective_date),
    ).fetchone()


def list_employee_service_prices(connection, work_id, service_id):
    service = connection.execute(
        "SELECT id FROM servicos WHERE id=? AND obra_id=?", (service_id, work_id)
    ).fetchone()
    if not service:
        raise ValueError("Serviço não pertence à obra selecionada")
    return [dict(row) for row in connection.execute(
        """SELECT p.*,f.nome funcionario,u.nome criado_por_nome
           FROM funcionario_servico_precos p
           JOIN funcionarios f ON f.id=p.funcionario_id
           LEFT JOIN usuarios u ON u.id=p.criado_por
           WHERE p.obra_id=? AND p.servico_id=? AND p.ativo=1
           ORDER BY f.nome,p.inicio_vigencia DESC,p.id DESC""",
        (work_id, service_id),
    )]


def current_fortnight(today=None):
    today = today or date.today()
    start = date(today.year, today.month, 1 if today.day <= 15 else 16)
    end = date(today.year, today.month, 15) if today.day <= 15 else date(
        today.year, today.month, calendar.monthrange(today.year, today.month)[1]
    )
    return start, end


def create_employee_service_price(connection, payload, user_id=None, ip=None, today=None):
    required = ("obra_id", "funcionario_id", "servico_id", "preco_pagamento", "motivo")
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise ValueError("Campos obrigatórios: " + ", ".join(missing))
    work_id = int(payload["obra_id"])
    employee_id = int(payload["funcionario_id"])
    service_id = int(payload["servico_id"])
    reason = str(payload["motivo"] or "").strip()
    if not reason:
        raise ValueError("Informe o motivo do preço específico")
    try:
        pay = float(payload["preco_pagamento"])
    except (TypeError, ValueError) as exc:
        raise ValueError("O preço deve ser numérico") from exc
    if not math.isfinite(pay) or pay < 0:
        raise ValueError("O preço não pode ser negativo ou inválido")
    employee = connection.execute(
        "SELECT * FROM funcionarios WHERE id=? AND obra_id=? AND ativo=1", (employee_id, work_id)
    ).fetchone()
    service = connection.execute(
        "SELECT * FROM servicos WHERE id=? AND obra_id=? AND ativo=1", (service_id, work_id)
    ).fetchone()
    if not employee or not service:
        raise ValueError("Funcionário ou serviço ativo não pertence à obra selecionada")
    period_start, period_end = current_fortnight(today)
    start = period_start.isoformat()
    with connection:
        exact = connection.execute(
            """SELECT * FROM funcionario_servico_precos
               WHERE funcionario_id=? AND servico_id=? AND inicio_vigencia=?""",
            (employee_id, service_id, start),
        ).fetchone()
        before = dict(exact) if exact else None
        if exact:
            cursor = connection.execute(
                """UPDATE funcionario_servico_precos SET preco_pagamento=?,motivo=?,atualizado_por=?,
                   atualizado_em=CURRENT_TIMESTAMP,versao=versao+1
                   WHERE id=? AND versao=?""",
                (pay, reason, user_id, exact["id"], exact["versao"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Este preço foi alterado por outro usuário. Recarregue os valores.")
            price_id = exact["id"]
        else:
            previous = connection.execute(
                """SELECT * FROM funcionario_servico_precos
                   WHERE funcionario_id=? AND servico_id=? AND ativo=1 AND inicio_vigencia<?
                     AND (fim_vigencia IS NULL OR fim_vigencia>=?)
                   ORDER BY inicio_vigencia DESC,id DESC LIMIT 1""",
                (employee_id, service_id, start, start),
            ).fetchone()
            next_price = connection.execute(
                """SELECT * FROM funcionario_servico_precos
                   WHERE funcionario_id=? AND servico_id=? AND ativo=1 AND inicio_vigencia>?
                   ORDER BY inicio_vigencia,id LIMIT 1""",
                (employee_id, service_id, start),
            ).fetchone()
            if previous:
                connection.execute(
                    """UPDATE funcionario_servico_precos SET fim_vigencia=?,atualizado_por=?,
                       atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=?""",
                    ((period_start - timedelta(days=1)).isoformat(), user_id, previous["id"]),
                )
            end = (date.fromisoformat(next_price["inicio_vigencia"]) - timedelta(days=1)).isoformat() if next_price else None
            cursor = connection.execute(
                """INSERT INTO funcionario_servico_precos
                   (obra_id,funcionario_id,servico_id,preco_pagamento,inicio_vigencia,fim_vigencia,motivo,criado_por)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (work_id, employee_id, service_id, pay, start, end, reason, user_id),
            )
            price_id = cursor.lastrowid
        price = dict(connection.execute(
            "SELECT * FROM funcionario_servico_precos WHERE id=?", (price_id,)
        ).fetchone())

        entries = connection.execute(
            """SELECT * FROM lancamentos WHERE obra_id=? AND funcionario_id=? AND servico_id=?
               AND ativo=1 AND data BETWEEN ? AND ? ORDER BY id""",
            (work_id, employee_id, service_id, period_start.isoformat(), period_end.isoformat()),
        ).fetchall()
        updated = skipped_locked = 0
        for entry in entries:
            linked = connection.execute(
                "SELECT 1 FROM itens_pagamento WHERE lancamento_id=? LIMIT 1", (entry["id"],)
            ).fetchone()
            if entry["pms_numero"] is not None or linked:
                skipped_locked += 1
                continue
            effective = service_with_effective_price(connection, service, entry["data"], employee_id)
            new_pay, _ = calculate_entry(
                effective, entry["quantidade_m2"], entry["horas_trabalhadas"], entry["extra"], entry["desconto"]
            )
            if round(float(entry["valor_pagar"]), 2) == new_pay:
                continue
            cursor = connection.execute(
                """UPDATE lancamentos SET valor_pagar=?,atualizado_por=?,atualizado_em=?,versao=versao+1
                   WHERE id=? AND versao=?""",
                (new_pay, user_id, datetime.now(timezone.utc).isoformat(), entry["id"], entry["versao"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Um lançamento foi alterado durante a atualização. Nenhuma alteração foi aplicada.")
            after = dict(connection.execute("SELECT * FROM lancamentos WHERE id=?", (entry["id"],)).fetchone())
            audit(
                connection, "lancamento", entry["id"], "preco_funcionario_aplicado", user_id,
                dict(entry), after, ip, details={"preco_funcionario_id": price_id, "motivo": reason},
            )
            updated += 1
        audit(
            connection, "funcionario_servico_preco", price_id,
            "corrigido" if before else "nova_vigencia", user_id, before, price, ip,
            details={"motivo": reason, "periodo_atualizado_inicio": period_start.isoformat(),
                     "periodo_atualizado_fim": period_end.isoformat(), "lancamentos_atualizados": updated,
                     "lancamentos_bloqueados": skipped_locked},
        )
    return {"preco": price, "criado": not bool(before),
            "periodo": {"inicio": period_start.isoformat(), "fim": period_end.isoformat()},
            "impacto": {"lancamentos_atualizados": updated, "lancamentos_bloqueados": skipped_locked}}


def service_with_effective_price(connection, service, effective_date, employee_id=None):
    result = dict(service)
    price = service_price_at(connection, service["id"], effective_date)
    if price:
        result["preco_pagamento"] = price["preco_pagamento"]
        result["valor_receber_unitario"] = price["valor_receber_unitario"]
    if employee_id is not None:
        employee_price = employee_service_price_at(connection, employee_id, service["id"], effective_date)
        if employee_price:
            result["preco_pagamento"] = employee_price["preco_pagamento"]
    return result


def create_service_price(connection, payload, user_id=None, ip=None):
    required = ("servico_id", "obra_id", "preco_pagamento", "valor_receber_unitario", "inicio_vigencia", "motivo")
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise ValueError("Campos obrigatórios: " + ", ".join(missing))
    service = connection.execute(
        "SELECT * FROM servicos WHERE id=? AND obra_id=?",
        (int(payload["servico_id"]), int(payload["obra_id"])),
    ).fetchone()
    if not service:
        raise ValueError("Serviço não pertence à obra selecionada")
    start = date.fromisoformat(payload["inicio_vigencia"])
    pay = float(payload["preco_pagamento"])
    receive = float(payload["valor_receber_unitario"])
    if pay < 0 or receive < 0:
        raise ValueError("Preços não podem ser negativos")
    latest = connection.execute(
        "SELECT * FROM servico_precos WHERE servico_id=? AND ativo=1 ORDER BY inicio_vigencia DESC,id DESC LIMIT 1",
        (service["id"],),
    ).fetchone()
    if latest and start <= date.fromisoformat(latest["inicio_vigencia"]):
        raise ValueError("A nova vigência deve ser posterior à vigência mais recente")
    try:
        with connection:
            if latest:
                previous_end = (start - timedelta(days=1)).isoformat()
                connection.execute(
                    "UPDATE servico_precos SET fim_vigencia=?,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=?",
                    (previous_end, user_id, latest["id"]),
                )
            cursor = connection.execute(
                """INSERT INTO servico_precos
                   (servico_id,obra_id,preco_pagamento,valor_receber_unitario,inicio_vigencia,motivo,criado_por)
                   VALUES(?,?,?,?,?,?,?)""",
                (service["id"], service["obra_id"], pay, receive, start.isoformat(), payload["motivo"].strip(), user_id),
            )
            if start <= date.today():
                connection.execute(
                    """UPDATE servicos SET preco_pagamento=?,valor_receber_unitario=?,
                       atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=?""",
                    (pay, receive, service["id"]),
                )
            audit(
                connection, "servico_preco", cursor.lastrowid, "nova_vigencia", user_id,
                after={**payload, "preco_pagamento": pay, "valor_receber_unitario": receive}, ip=ip,
            )
    except sqlite3.IntegrityError as error:
        raise ValueError("Já existe preço para esta data de vigência") from error
    return dict(connection.execute("SELECT * FROM servico_precos WHERE id=?", (cursor.lastrowid,)).fetchone())


def correct_service_price(connection, price_id, payload, user_id=None, ip=None):
    before = connection.execute("SELECT * FROM servico_precos WHERE id=? AND ativo=1", (price_id,)).fetchone()
    if not before:
        raise ValueError("Preço não encontrado")
    reason = (payload.get("motivo_correcao") or "").strip()
    if not reason:
        raise ValueError("O motivo da correção é obrigatório")
    expected_version = int(payload.get("versao", before["versao"]))
    pay = float(payload.get("preco_pagamento", before["preco_pagamento"]))
    receive = float(payload.get("valor_receber_unitario", before["valor_receber_unitario"]))
    if pay < 0 or receive < 0:
        raise ValueError("Preços não podem ser negativos")
    impacted = connection.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN pms_numero IS NOT NULL THEN 1 ELSE 0 END) medidos
           FROM lancamentos WHERE servico_id=? AND data>=?
             AND (? IS NULL OR data<=?)""",
        (before["servico_id"], before["inicio_vigencia"], before["fim_vigencia"], before["fim_vigencia"]),
    ).fetchone()
    with connection:
        cursor = connection.execute(
            """UPDATE servico_precos SET preco_pagamento=?,valor_receber_unitario=?,
               motivo=?,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP,versao=versao+1
               WHERE id=? AND versao=?""",
            (pay, receive, reason, user_id, price_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise ValueError("Este preço foi alterado por outro usuário. Recarregue o histórico.")
        current = service_price_at(connection, before["servico_id"], date.today().isoformat())
        if current and current["id"] == price_id:
            connection.execute(
                """UPDATE servicos SET preco_pagamento=?,valor_receber_unitario=?,
                   atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=?""",
                (pay, receive, before["servico_id"]),
            )
        after = dict(connection.execute("SELECT * FROM servico_precos WHERE id=?", (price_id,)).fetchone())
        audit(
            connection, "servico_preco", price_id, "corrigido", user_id, dict(before), after, ip,
            details={"motivo": reason, "lancamentos_afetados": impacted["total"], "lancamentos_medidos": impacted["medidos"] or 0},
        )
    return {"preco": after, "impacto": {"lancamentos": impacted["total"], "medidos": impacted["medidos"] or 0}}


def dashboard(connection, obra_id=1, start=None, end=None):
    where = ["obra_id = ?", "ativo=1"]
    params = [obra_id]
    if start:
        where.append("data >= ?")
        params.append(start)
    if end:
        where.append("data <= ?")
        params.append(end)
    clause = " AND ".join(where)
    row = connection.execute(
        f"SELECT COUNT(*) total_lancamentos, COALESCE(SUM(valor_pagar),0) total_pagar, COALESCE(SUM(valor_receber),0) total_receber, COALESCE(SUM(extra),0) extras, COALESCE(SUM(desconto),0) descontos FROM lancamentos WHERE {clause}", params
    ).fetchone()
    active = connection.execute("SELECT COUNT(*) FROM funcionarios WHERE obra_id=? AND lower(COALESCE(situacao,'')) IN ('efetivado','efetivada')", (obra_id,)).fetchone()[0]
    return {**dict(row), "equipe_ativa": active, "margem": round(row["total_receber"] - row["total_pagar"], 2)}


def deactivate_entry(connection, entry_id, reason, user_id=None, ip=None):
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Informe o motivo da exclusão do lançamento")
    row = connection.execute("SELECT * FROM lancamentos WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise ValueError("Lançamento não encontrado")
    if not row["ativo"]:
        raise ValueError("Este lançamento já foi excluído")
    linked = connection.execute("SELECT 1 FROM itens_pagamento WHERE lancamento_id=? LIMIT 1", (entry_id,)).fetchone()
    if row["pms_numero"] is not None or linked:
        raise ValueError("Lançamento já medido ou vinculado a fechamento não pode ser excluído; use retificação")
    connection.execute("UPDATE lancamentos SET ativo=0,atualizado_por=?,atualizado_em=? WHERE id=?", (user_id, datetime.now(timezone.utc).isoformat(), entry_id))
    after = dict(connection.execute("SELECT * FROM lancamentos WHERE id=?", (entry_id,)).fetchone())
    audit(connection, "lancamento", entry_id, "excluido", user_id, dict(row), after, ip, details={"motivo": reason})
    connection.commit()
    return {"ok": True, "historico_preservado": True}


def create_entry(connection: sqlite3.Connection, payload: dict, user_id=None, ip=None, commit=True):
    required = ("obra_id", "data", "funcionario_id", "servico_id", "tipo")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError("Campos obrigatórios: " + ", ".join(missing))
    if payload["tipo"] not in {"produção", "diaria", "apropriado"}:
        raise ValueError("Tipo de lançamento inválido")
    for field, label in (
        ("quantidade_m2", "Quantidade"), ("horas_trabalhadas", "Horas"),
        ("extra", "Extra"), ("desconto", "Desconto"),
    ):
        value = payload.get(field)
        if value in (None, ""):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} deve ser um número válido") from error
        if numeric < 0:
            raise ValueError(f"{label} não pode ter valor negativo")
        payload[field] = numeric
    employee = connection.execute("SELECT * FROM funcionarios WHERE id=? AND obra_id=? AND ativo=1", (payload["funcionario_id"], payload["obra_id"])).fetchone()
    service = connection.execute("SELECT * FROM servicos WHERE id=? AND obra_id=? AND ativo=1", (payload["servico_id"], payload["obra_id"])).fetchone()
    if not employee or not service:
        raise ValueError("Funcionário ou serviço ativo não pertence à obra selecionada")
    work_date = date.fromisoformat(payload["data"])
    effective_price = service_with_effective_price(
        connection, service, work_date.isoformat(), payload["funcionario_id"]
    )
    pay, receive = calculate_entry(effective_price, payload.get("quantidade_m2"), payload.get("horas_trabalhadas"), payload.get("extra"), payload.get("desconto"))
    weekdays = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    months = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    columns = ("obra_id","data","dia_semana","mes","funcionario_id","tipo","servico_id","quantidade_m2","horas_trabalhadas","desconto","extra","bloco","ap","casa","valor_pagar","valor_receber","obs","pms_numero","criado_por","atualizado_por","atualizado_em","apontamento_origem_id","funcionario_origem_id")
    values = (payload["obra_id"], payload["data"], weekdays[work_date.weekday()], months[work_date.month-1], payload["funcionario_id"], payload["tipo"], payload["servico_id"], payload.get("quantidade_m2"), payload.get("horas_trabalhadas"), payload.get("desconto") or 0, payload.get("extra") or 0, payload.get("bloco"), payload.get("ap"), payload.get("casa"), pay, receive, payload.get("obs"), payload.get("pms_numero"), user_id, user_id, datetime.now(timezone.utc).isoformat(), payload.get("apontamento_origem_id"), payload.get("funcionario_origem_id"))
    cursor = connection.execute(f"INSERT INTO lancamentos ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
    audit(connection, "lancamento", cursor.lastrowid, "criado", user_id, after={**payload, "valor_pagar": pay, "valor_receber": receive}, ip=ip)
    if commit:
        connection.commit()
    return {"id": cursor.lastrowid, "valor_pagar": pay, "valor_receber": receive}


def update_entry(connection, entry_id, payload, user_id=None, ip=None, allow_retification=False):
    before = connection.execute("SELECT * FROM lancamentos WHERE id=?", (entry_id,)).fetchone()
    if not before:
        raise ValueError("Lançamento não encontrado")
    if not before["ativo"]:
        raise ValueError("Lançamento excluído não pode ser alterado")
    linked = connection.execute("SELECT 1 FROM itens_pagamento WHERE lancamento_id=? LIMIT 1", (entry_id,)).fetchone()
    locked = before["pms_numero"] is not None or linked is not None
    reason = (payload.get("motivo_alteracao") or "").strip()
    if locked and not allow_retification:
        raise PermissionError("Lançamento já medido/fechado; utilize retificação administrativa")
    if locked and not reason:
        raise ValueError("O motivo da retificação é obrigatório")
    merged = {**dict(before), **payload}
    employee = connection.execute(
        "SELECT * FROM funcionarios WHERE id=? AND obra_id=? AND ativo=1",
        (merged["funcionario_id"], merged["obra_id"]),
    ).fetchone()
    service = connection.execute(
        "SELECT * FROM servicos WHERE id=? AND obra_id=? AND ativo=1",
        (merged["servico_id"], merged["obra_id"]),
    ).fetchone()
    if not employee or not service:
        raise ValueError("Funcionário ou serviço ativo não pertence à obra selecionada")
    effective_price = service_with_effective_price(
        connection, service, merged["data"], merged["funcionario_id"]
    )
    pay, receive = calculate_entry(effective_price, merged.get("quantidade_m2"), merged.get("horas_trabalhadas"), merged.get("extra"), merged.get("desconto"))
    work_date = date.fromisoformat(merged["data"])
    weekdays = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    months = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    merged["dia_semana"] = weekdays[work_date.weekday()]
    merged["mes"] = months[work_date.month - 1]
    editable = ("data","dia_semana","mes","funcionario_id","tipo","servico_id","quantidade_m2","horas_trabalhadas","desconto","extra","bloco","ap","casa","obs","pms_numero")
    values = [merged.get(key) for key in editable]
    cursor = connection.execute(
        f"UPDATE lancamentos SET {','.join(key+'=?' for key in editable)},valor_pagar=?,valor_receber=?,atualizado_por=?,atualizado_em=?,versao=versao+1 WHERE id=? AND versao=?",
        [*values, pay, receive, user_id, datetime.now(timezone.utc).isoformat(), entry_id, int(payload.get("versao", before["versao"]))],
    )
    if cursor.rowcount != 1:
        raise ValueError("Este lançamento foi alterado por outro usuário. Recarregue a página.")
    after = dict(connection.execute("SELECT * FROM lancamentos WHERE id=?", (entry_id,)).fetchone())
    audit(connection, "lancamento", entry_id, "retificado" if locked else "alterado", user_id, dict(before), after, ip, details={"motivo": reason} if reason else None)
    connection.commit()
    return after


def create_appropriation(connection, payload, user_id=None, ip=None):
    required = ("obra_id", "data", "funcionario_id", "servico_id")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError("Campos obrigatórios: " + ", ".join(missing))
    employee = connection.execute(
        "SELECT 1 FROM funcionarios WHERE id=? AND obra_id=? AND ativo=1",
        (payload["funcionario_id"], payload["obra_id"]),
    ).fetchone()
    service = connection.execute(
        "SELECT 1 FROM servicos WHERE id=? AND obra_id=? AND ativo=1",
        (payload["servico_id"], payload["obra_id"]),
    ).fetchone()
    if not employee or not service:
        raise ValueError("Funcionário ou serviço ativo não pertence à obra selecionada")
    work_date = date.fromisoformat(payload["data"])
    default_end = "16:00" if work_date.weekday() == 4 else "17:00"
    fields = (
        "obra_id","data","funcionario_id","servico_id","pms_numero",
        "inicio_manha","termino_manha","inicio_tarde","termino_tarde",
        "assinatura_encarregado","observacao","criado_por","atualizado_por",
    )
    values = [
        payload.get(key) for key in fields[:-2]
    ] + [user_id, user_id]
    if not values[8]:
        values[8] = default_end
    cursor = connection.execute(
        f"INSERT INTO apropriacoes({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
        values,
    )
    audit(connection, "apropriacao", cursor.lastrowid, "criada", user_id, after=payload, ip=ip)
    connection.commit()
    return {"id": cursor.lastrowid, "termino_padrao": default_end}


def save_quality(connection, launch_id, situation, observation, user_id=None, ip=None):
    if situation not in ("ok", "pendente", "nao_avaliado"):
        raise ValueError("Situação de qualidade inválida")
    observation = (observation or "").strip() or None
    if situation == "pendente" and not observation:
        raise ValueError("Descreva o problema encontrado na avaliação de qualidade")
    before = connection.execute("SELECT * FROM avaliacoes_qualidade WHERE lancamento_id=?", (launch_id,)).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT INTO avaliacoes_qualidade(lancamento_id,situacao,observacao,tecnico_id,avaliado_em,atualizado_em)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(lancamento_id) DO UPDATE SET situacao=excluded.situacao,observacao=excluded.observacao,
           tecnico_id=excluded.tecnico_id,avaliado_em=excluded.avaliado_em,atualizado_em=excluded.atualizado_em""",
        (launch_id, situation, observation, user_id, now if situation != "nao_avaliado" else None, now),
    )
    after = dict(connection.execute("SELECT * FROM avaliacoes_qualidade WHERE lancamento_id=?", (launch_id,)).fetchone())
    connection.execute(
        "INSERT INTO qualidade_historico(lancamento_id,situacao,observacao,tecnico_id,registrado_em) VALUES(?,?,?,?,?)",
        (launch_id, situation, observation, user_id, now),
    )
    audit(connection, "qualidade", launch_id, "avaliada", user_id, dict(before) if before else None, after, ip)
    connection.commit()
    return after


def generate_closing(connection, obra_id, start, end, pms_number, user_id=None, ip=None):
    cursor = connection.execute(
        """INSERT INTO fechamentos_pagamento(obra_id,pms_numero,periodo_inicio,periodo_fim,criado_por)
           VALUES(?,?,?,?,?)
           ON CONFLICT(obra_id,pms_numero,periodo_inicio,periodo_fim)
           DO UPDATE SET atualizado_em=CURRENT_TIMESTAMP RETURNING id""",
        (obra_id, pms_number, start, end, user_id),
    )
    closing_id = cursor.fetchone()[0]
    rows = connection.execute(
        "SELECT * FROM lancamentos WHERE obra_id=? AND ativo=1 AND data BETWEEN ? AND ? AND (? IS NULL OR pms_numero=? OR pms_numero IS NULL)",
        (obra_id, start, end, pms_number, pms_number),
    ).fetchall()
    for row in rows:
        connection.execute(
            """INSERT OR IGNORE INTO itens_pagamento
               (fechamento_id,lancamento_id,funcionario_id,servico_id,quantidade,valor_padrao,valor_final)
               VALUES(?,?,?,?,?,?,?)""",
            (closing_id, row["id"], row["funcionario_id"], row["servico_id"], row["quantidade_m2"] or row["horas_trabalhadas"] or 1, row["valor_pagar"], row["valor_pagar"]),
        )
    recalculate_closing(connection, closing_id)
    audit(connection, "fechamento", closing_id, "gerado", user_id, after={"periodo_inicio": start, "periodo_fim": end, "pms": pms_number}, ip=ip)
    connection.commit()
    return {"id": closing_id, "itens": len(rows)}


def recalculate_closing(connection, closing_id):
    services = connection.execute("SELECT COALESCE(SUM(valor_final),0) FROM itens_pagamento WHERE fechamento_id=?", (closing_id,)).fetchone()[0]
    extras = connection.execute("SELECT COALESCE(SUM(valor),0) FROM extras_pagamento WHERE fechamento_id=? AND ativo=1", (closing_id,)).fetchone()[0]
    discounts = connection.execute("SELECT COALESCE(SUM(valor),0) FROM descontos_pagamento WHERE fechamento_id=? AND ativo=1", (closing_id,)).fetchone()[0]
    receive = connection.execute(
        "SELECT COALESCE(SUM(l.valor_receber),0) FROM itens_pagamento i JOIN lancamentos l ON l.id=i.lancamento_id WHERE i.fechamento_id=?",
        (closing_id,),
    ).fetchone()[0]
    total = round(services + extras - discounts, 2)
    connection.execute(
        "UPDATE fechamentos_pagamento SET total_servicos=?,total_extras=?,total_descontos=?,total_pagar=?,total_receber=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
        (services, extras, discounts, total, receive, closing_id),
    )
    return {"servicos": services, "extras": extras, "descontos": discounts, "total_pagar": total, "total_receber": receive, "margem": round(receive-total, 2)}


def adjust_payment_item(connection, item_id, new_value, justification, user_id, ip=None):
    if not justification:
        raise ValueError("A justificativa do ajuste é obrigatória")
    item = connection.execute("SELECT * FROM itens_pagamento WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise ValueError("Item de pagamento não encontrado")
    connection.execute("UPDATE ajustes_pagamento SET ativo=0 WHERE item_pagamento_id=? AND ativo=1", (item_id,))
    connection.execute(
        "INSERT INTO ajustes_pagamento(item_pagamento_id,valor_anterior,valor_novo,justificativa,usuario_id) VALUES(?,?,?,?,?)",
        (item_id, item["valor_final"], float(new_value), justification, user_id),
    )
    connection.execute("UPDATE itens_pagamento SET valor_ajustado=?,valor_final=? WHERE id=?", (float(new_value), float(new_value), item_id))
    totals = recalculate_closing(connection, item["fechamento_id"])
    audit(connection, "item_pagamento", item_id, "ajustado", user_id, {"valor": item["valor_final"]}, {"valor": float(new_value), "justificativa": justification}, ip)
    connection.commit()
    return totals


def payment_component(connection, kind, payload, user_id, ip=None):
    table = "descontos_pagamento" if kind == "desconto" else "extras_pagamento"
    if float(payload.get("valor") or 0) < 0:
        raise ValueError("O valor não pode ser negativo")
    if kind == "desconto":
        columns = ("fechamento_id","funcionario_id","tipo","descricao","valor","data","usuario_id")
    else:
        columns = ("fechamento_id","funcionario_id","servico_id","descricao","justificativa","valor","data","usuario_id")
    values = [payload.get(key) for key in columns[:-1]] + [user_id]
    cursor = connection.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
    totals = recalculate_closing(connection, payload["fechamento_id"])
    audit(connection, table, cursor.lastrowid, "criado", user_id, after=payload, ip=ip)
    connection.commit()
    return {"id": cursor.lastrowid, "totais": totals}


def remove_payment_component(connection, kind, component_id, user_id, ip=None):
    table = "descontos_pagamento" if kind == "desconto" else "extras_pagamento"
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (component_id,)).fetchone()
    if not row:
        raise ValueError("Registro financeiro não encontrado")
    connection.execute(f"UPDATE {table} SET ativo=0,atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (component_id,))
    totals = recalculate_closing(connection, row["fechamento_id"])
    audit(connection, table, component_id, "removido", user_id, before=dict(row), ip=ip)
    connection.commit()
    return totals
