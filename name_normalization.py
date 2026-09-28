"""Normalização Unicode de nomes dos cadastros ativos, preservando documentos históricos."""
from __future__ import annotations

import json


def normalize_field_record(kind, record):
    result = dict(record)
    fields = {
        "employees": ("name",),
        "services": ("name",),
        "locations": ("label",),
        "settings": ("companyName", "workspaceName", "responsible"),
    }.get(kind, ())
    for field in fields:
        if isinstance(result.get(field), str):
            result[field] = result[field].upper()
    return result


def normalize_current_names(connection):
    """Uppercase nomes mestre e catálogos atuais, sem alterar snapshots nem auditorias antigas."""
    specs = (
        ("obras", "id", None, ("nome", "cliente_contratante", "fornecedor", "engenheiro_responsavel", "encarregado"), True),
        ("funcionarios", "id", "obra_id", ("nome",), True),
        ("servicos", "id", "obra_id", ("nome_interno",), True),
        ("usuarios", "id", None, ("nome",), True),
        ("configuracoes_empresa", "id", None, ("nome",), False),
        ("configuracoes_obra", "obra_id", None, ("responsavel_apontamento",), False),
    )
    unique_names = (("obras", "nome", None), ("funcionarios", "nome", "obra_id"), ("servicos", "nome_interno", "obra_id"))
    for table, field, scope in unique_names:
        select = f"SELECT {scope + ',' if scope else ''}{field},id FROM {table} WHERE {field} IS NOT NULL AND TRIM({field})<>''"
        seen = {}
        for row in connection.execute(select):
            key = (row[0], row[1].upper()) if scope else row[0].upper()
            record_id = row[2] if scope else row[1]
            previous = seen.setdefault(key, record_id)
            if previous != record_id:
                raise RuntimeError(f"Nomes duplicados após maiúsculas em {table}.{field}; migração interrompida sem alterar os dados")

    changes = {}
    for table, id_field, _scope, fields, versioned in specs:
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        changed = 0
        for row in rows:
            updates = {field: row[field].upper() for field in fields if isinstance(row[field], str) and row[field] != row[field].upper()}
            if not updates:
                continue
            assignments = [f"{field}=?" for field in updates]
            values = list(updates.values())
            if versioned and "versao" in row.keys():
                assignments.append("versao=versao+1")
            connection.execute(
                f"UPDATE {table} SET {','.join(assignments)} WHERE {id_field}=?",
                [*values, row[id_field]],
            )
            changed += 1
        if changed:
            changes[table] = changed

    records = connection.execute(
        "SELECT obra_id,kind,id,data FROM apontamento_records WHERE kind IN ('employees','services','locations','settings')"
    ).fetchall()
    for row in records:
        try:
            record = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        normalized = normalize_field_record(row["kind"], record)
        if normalized != record:
            connection.execute(
                "UPDATE apontamento_records SET data=?,atualizado_em=CURRENT_TIMESTAMP WHERE obra_id=? AND kind=? AND id=?",
                (json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), row["obra_id"], row["kind"], row["id"]),
            )
            changes[f"apontamento_records.{row['kind']}"] = changes.get(f"apontamento_records.{row['kind']}", 0) + 1

    if changes:
        connection.execute(
            "INSERT INTO auditoria(entidade,acao,detalhes) VALUES('banco','nomes_normalizados',?)",
            (json.dumps(changes, ensure_ascii=False, sort_keys=True),),
        )
    return changes
