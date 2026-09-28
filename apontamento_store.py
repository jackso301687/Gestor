"""Persistência isolada do PWA de apontamento dentro do banco PMS."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from name_normalization import normalize_field_record


KINDS = ("employees", "services", "locations", "appointments", "settings", "audit")
MAX_RECORDS_PER_KIND = 25_000
MAX_RECORD_BYTES = 256_000


def validate_record(kind, row):
    """Valida o formato consumido pelas telas antes de persistir um lote."""
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not re.fullmatch(r"[\w:.-]{1,200}", row["id"], re.ASCII):
        raise ValueError(f"Registro sem id válido na store {kind}")
    if not isinstance(row.get("updatedAt"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", row["updatedAt"]):
        raise ValueError(f"Registro sem updatedAt válido na store {kind}")
    try:
        stamp = datetime.fromisoformat(row["updatedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError(f"Registro sem updatedAt válido na store {kind}") from None
    for key in ("active", "_deleted", "requiresQuantity", "blockFutureDates"):
        if key in row and not isinstance(row[key], bool):
            raise ValueError(f"{kind}: {key} deve ser booleano")
    for key in ("name", "label", "role", "team", "category", "unit", "type", "observation", "companyName", "workspaceName", "responsible", "action", "entity", "entityId", "details"):
        if key in row and (not isinstance(row[key], str) or len(row[key]) > 10000):
            raise ValueError(f"{kind}: texto inválido em {key}")
    if row.get("_deleted"):
        return
    required = {"employees": ("name",), "services": ("name",), "locations": ("label",), "audit": ("action", "entity")}
    for key in required.get(kind, ()):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"{kind}: {key} é obrigatório")
    if kind == "settings" and row["id"] != "app":
        raise ValueError("Identificador de configuração inválido")
    if kind == "appointments":
        try:
            if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError("Data do apontamento inválida") from None
        if row.get("status") not in ("em_andamento", "finalizado", "pendente", "pausado"):
            raise ValueError("Status do apontamento inválido")
        for key in ("employeeIds", "locationIds"):
            ids = row.get(key)
            if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids):
                raise ValueError(f"Apontamento sem {key} válidos")
        if not isinstance(row.get("serviceId"), str) or not row["serviceId"]:
            raise ValueError("Apontamento sem serviço")
        quantity = row.get("quantity")
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity < 0):
            raise ValueError("Quantidade inválida")
        for key in ("startTime", "endTime"):
            if row.get(key) and (not isinstance(row[key], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", row[key])):
                raise ValueError("Horário inválido")


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("O conteúdo da sincronização deve ser um objeto JSON")
    unknown = set(payload) - set(KINDS)
    if unknown:
        raise ValueError(f"Store de apontamento desconhecida: {sorted(unknown)[0]}")
    validated = {}
    for kind in KINDS:
        rows = payload.get(kind, [])
        if not isinstance(rows, list):
            raise ValueError(f"A store {kind} deve ser uma lista")
        if len(rows) > MAX_RECORDS_PER_KIND:
            raise ValueError(f"A store {kind} excede o limite de registros")
        validated[kind] = []
        for source_row in rows:
            row = normalize_field_record(kind, source_row)
            validate_record(kind, row)
            record_id = row.get("id")
            updated_at = row.get("updatedAt")
            if len(updated_at) > 80:
                raise ValueError(f"Registro sem updatedAt válido na store {kind}")
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
                raise ValueError(f"Registro muito grande na store {kind}")
            validated[kind].append((record_id, updated_at, encoded))
    return validated


def snapshot(connection, obra_id):
    result = {kind: [] for kind in KINDS}
    rows = connection.execute(
        """SELECT kind,data FROM apontamento_records
           WHERE obra_id=? ORDER BY kind,updated_at,id""",
        (int(obra_id),),
    ).fetchall()
    for row in rows:
        if row["kind"] in result:
            result[row["kind"]].append(json.loads(row["data"]))
    return result


def merge_payload(connection, obra_id, payload, user_id=None, ip=None):
    validated = validate_payload(payload)
    changed = 0
    for kind, rows in validated.items():
        for record_id, updated_at, encoded in rows:
            cursor = connection.execute(
                """INSERT INTO apontamento_records
                       (obra_id,kind,id,updated_at,data,atualizado_por,atualizado_em)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(obra_id,kind,id) DO UPDATE SET
                     updated_at=excluded.updated_at,
                     data=excluded.data,
                     atualizado_por=excluded.atualizado_por,
                     atualizado_em=CURRENT_TIMESTAMP
                   WHERE julianday(excluded.updated_at) >= julianday(apontamento_records.updated_at)
                     AND excluded.data != apontamento_records.data""",
                (int(obra_id), kind, record_id, updated_at, encoded, user_id),
            )
            changed += max(cursor.rowcount, 0)
    # Relacionamentos são resolvidos na mesma obra e transação, após receber os cadastros.
    for record_id, _, _ in validated["appointments"]:
        record = json.loads(connection.execute(
            "SELECT data FROM apontamento_records WHERE obra_id=? AND kind='appointments' AND id=?",
            (obra_id, record_id),
        ).fetchone()[0])
        if record.get("_deleted"):
            continue
        for kind, ids in (("employees", record["employeeIds"]), ("locations", record["locationIds"]), ("services", [record["serviceId"]])):
            for linked_id in ids:
                if not connection.execute("SELECT 1 FROM apontamento_records WHERE obra_id=? AND kind=? AND id=?", (obra_id, kind, linked_id)).fetchone():
                    raise ValueError(f"Apontamento referencia {kind} inexistente nesta obra")
    if changed:
        connection.execute(
            """INSERT INTO auditoria(entidade,acao,detalhes,usuario_id,ip)
               VALUES('apontamento_pwa','sincronizacao',?,?,?)""",
            (json.dumps({"obra_id": int(obra_id), "registros_processados": changed}), user_id, ip),
        )
    connection.commit()
    return {"ok": True, "processed": changed, "snapshot": snapshot(connection, obra_id)}


def import_sqlite_source(connection, source_path: Path, obra_id: int):
    """Importa o banco legado sem apagar registros novos do destino."""
    source_path = Path(source_path)
    if not source_path.is_file():
        return {kind: 0 for kind in KINDS}
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    imported = {kind: 0 for kind in KINDS}
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("A base de apontamento de origem falhou na verificação de integridade")
        for row in source.execute("SELECT kind,id,updated_at,data FROM records ORDER BY kind,id"):
            if row["kind"] not in KINDS:
                continue
            data = json.loads(row["data"])
            if data.get("id") != row["id"] or data.get("updatedAt") != row["updated_at"]:
                raise RuntimeError(f"Registro inconsistente na origem: {row['kind']}/{row['id']}")
            data = normalize_field_record(row["kind"], data)
            validate_record(row["kind"], data)
            cursor = connection.execute(
                """INSERT INTO apontamento_records(obra_id,kind,id,updated_at,data)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(obra_id,kind,id) DO UPDATE SET
                     updated_at=excluded.updated_at,data=excluded.data,atualizado_em=CURRENT_TIMESTAMP
                   WHERE julianday(excluded.updated_at) > julianday(apontamento_records.updated_at)""",
                (int(obra_id), row["kind"], row["id"], row["updated_at"], json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
            )
            imported[row["kind"]] += max(cursor.rowcount, 0)
        if sum(imported.values()):
            connection.execute(
                """INSERT INTO auditoria(entidade,acao,detalhes)
                   VALUES('apontamento_pwa','importacao_inicial',?)""",
                (json.dumps({"obra_id": int(obra_id), **imported}, ensure_ascii=False),),
            )
        connection.commit()
    finally:
        source.close()
    return imported
