"""Regras de persistência para configurações administrativas e apontamento de campo."""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from database import audit, create_entry, has_work_access
import database as database_module


FIELD_STORES = ("employees", "services", "locations", "appointments")
PROFILE_ACTIONS = {
    "admin": ["Configurações da empresa e das obras", "Usuários e acessos", "Todos os módulos e auditoria"],
    "engenharia": ["Lançamentos", "Apropriações", "Medições PMS", "Relatórios"],
    "qualidade": ["Avaliação de qualidade", "Consulta de medições", "Relatórios"],
    "financeiro": ["Pagamentos", "Ajustes financeiros", "Relatórios"],
    "operador": ["Apontamento", "Lançamentos", "Apropriações"],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_name(value):
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(char for char in raw if not unicodedata.combining(char)).casefold().split())


def _record(connection, work_id, kind, record_id):
    row = connection.execute(
        "SELECT data FROM apontamento_records WHERE obra_id=? AND kind=? AND id=?",
        (work_id, kind, str(record_id)),
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _company_row(connection):
    return dict(connection.execute("SELECT * FROM configuracoes_empresa WHERE id=1").fetchone())


def field_settings(connection, work_id):
    work = connection.execute(
        "SELECT o.nome,c.responsavel_apontamento,c.bloquear_datas_futuras,c.politica_coletivo "
        "FROM obras o JOIN configuracoes_obra c ON c.obra_id=o.id WHERE o.id=?",
        (work_id,),
    ).fetchone()
    if not work:
        raise ValueError("Obra não encontrada")
    company = connection.execute("SELECT nome FROM configuracoes_empresa WHERE id=1").fetchone()
    existing = _record(connection, work_id, "settings", "app") or {"id": "app"}
    return {
        **existing,
        "id": "app",
        "companyName": company["nome"] if company else "",
        "workspaceName": work["nome"],
        "responsible": work["responsavel_apontamento"],
        "accent": "#167a62",
        "blockFutureDates": bool(work["bloquear_datas_futuras"]),
        "collectiveQuantityPolicy": work["politica_coletivo"],
        "updatedAt": now_iso(),
    }


def sync_field_settings(connection, work_id, user_id=None, persist=True):
    settings = field_settings(connection, work_id)
    if persist:
        connection.execute(
            """INSERT INTO apontamento_records(obra_id,kind,id,updated_at,data,atualizado_por)
               VALUES(?, 'settings','app',?,?,?)
               ON CONFLICT(obra_id,kind,id) DO UPDATE SET updated_at=excluded.updated_at,
                 data=excluded.data,atualizado_por=excluded.atualizado_por,
                 atualizado_em=CURRENT_TIMESTAMP""",
            (work_id, settings["updatedAt"], json.dumps(settings, ensure_ascii=False), user_id),
        )
    return settings


def ensure_field_catalog(connection, work_id, user_id=None):
    """Publica no catálogo do campo os cadastros centrais ativos da obra.

    Os registros usam IDs próprios do catálogo central e não copiam lançamentos,
    apontamentos ou histórico financeiro. Cadastros criados no aparelho continuam
    preservados para que possam ser vinculados pela integração do Gestor.
    """
    now = now_iso()
    central_employees = connection.execute(
        """SELECT id,nome,profissao,situacao,ativo FROM funcionarios
           WHERE obra_id=? AND ativo=1
           ORDER BY nome""",
        (work_id,),
    ).fetchall()
    central_services = connection.execute(
        """SELECT id,nome_interno,categoria,unidade,ativo FROM servicos
           WHERE obra_id=? AND ativo=1
           ORDER BY nome_interno""",
        (work_id,),
    ).fetchall()

    records = []
    for row in central_employees:
        status = normalize_name(row["situacao"])
        records.append((
            "employees",
            f"central-employee-{row['id']}",
            {
                "id": f"central-employee-{row['id']}",
                "name": row["nome"],
                "role": row["profissao"] or "",
                "team": "",
                "active": not status.startswith("deslig") and not status.startswith("delig"),
                "updatedAt": now,
            },
        ))
    for row in central_services:
        records.append((
            "services",
            f"central-service-{row['id']}",
            {
                "id": f"central-service-{row['id']}",
                "name": row["nome_interno"],
                "category": row["categoria"] or "",
                "unit": row["unidade"] or "",
                "requiresQuantity": bool(row["unidade"]),
                "active": True,
                "updatedAt": now,
            },
        ))
    changed = 0
    for kind, record_id, record in records:
        existing = connection.execute(
            "SELECT data FROM apontamento_records WHERE obra_id=? AND kind=? AND id=?",
            (work_id, kind, record_id),
        ).fetchone()
        if existing:
            old = json.loads(existing["data"])
            comparable = {key: value for key, value in record.items() if key != "updatedAt"}
            old_comparable = {key: value for key, value in old.items() if key != "updatedAt"}
            if comparable == old_comparable:
                continue
        connection.execute(
            """INSERT INTO apontamento_records(obra_id,kind,id,updated_at,data,atualizado_por)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(obra_id,kind,id) DO UPDATE SET updated_at=excluded.updated_at,
                 data=excluded.data,atualizado_por=excluded.atualizado_por,
                 atualizado_em=CURRENT_TIMESTAMP""",
            (work_id, kind, record_id, now, json.dumps(record, ensure_ascii=False), user_id),
        )
        changed += 1
    return changed


def get_settings(connection, work_id, user, session_id):
    work = connection.execute(
        """SELECT o.*,c.responsavel_apontamento,c.bloquear_datas_futuras,c.politica_coletivo
           FROM obras o JOIN configuracoes_obra c ON c.obra_id=o.id WHERE o.id=?""",
        (work_id,),
    ).fetchone()
    if not work:
        raise ValueError("Obra não encontrada")
    company = _company_row(connection)
    company["logo_base64"] = base64.b64encode(company["logo_dados"]).decode("ascii") if company["logo_dados"] else ""
    company.pop("logo_dados", None)
    preferences = connection.execute(
        "SELECT * FROM preferencias_usuario WHERE usuario_id=?", (user["id"],)
    ).fetchone()
    preferences = dict(preferences) if preferences else {
        "obra_padrao_id": work_id,
        "tela_inicial": "overview",
        "linhas_por_pagina": 25,
    }
    view_permissions = {"entries": "lancamentos", "quality": "qualidade", "pms": "pms", "payments": "pagamentos"}
    if preferences["tela_inicial"] in view_permissions and not database_module.has_permission(user, view_permissions[preferences["tela_inicial"]]):
        preferences["tela_inicial"] = "overview"
    sessions = connection.execute(
        """SELECT id,ip,criado_em,expira_em,(id=?) atual FROM sessoes
           WHERE usuario_id=? AND expira_em>? ORDER BY atual DESC,criado_em DESC""",
        (session_id, user["id"], now_iso()),
    ).fetchall()
    works = connection.execute("SELECT id,nome,status FROM obras ORDER BY nome").fetchall()
    field_status = []
    for row in connection.execute(
        """SELECT kind,COUNT(*) total,MAX(updated_at) ultima_sincronizacao
           FROM apontamento_records WHERE obra_id=? GROUP BY kind ORDER BY kind""",
        (work_id,),
    ):
        field_status.append(dict(row))
    return {
        "company": company,
        "work": dict(work),
        "preferences": preferences,
        "works": [dict(row) for row in works if has_work_access(connection, user, row["id"])],
        "sessions": [dict(row) for row in sessions],
        "profiles": PROFILE_ACTIONS,
        "pwa": {"offline": True, "stores": list(FIELD_STORES)},
        "field_status": field_status,
    }


def update_company(connection, payload, user_id, ip=None):
    name = str(payload.get("nome") or "").strip().upper()
    cnpj = re.sub(r"\D", "", str(payload.get("cnpj") or ""))
    email = str(payload.get("email") or "").strip()
    phone = str(payload.get("telefone") or "").strip()
    address = str(payload.get("endereco") or "").strip()
    if len(name) > 180 or len(email) > 180 or len(phone) > 60 or len(address) > 300:
        raise ValueError("Um dos campos excede o tamanho permitido")
    if cnpj and len(cnpj) != 14:
        raise ValueError("O CNPJ deve conter 14 números")
    if email and ("@" not in email or len(email.split("@", 1)[0]) == 0):
        raise ValueError("Informe um e-mail válido")
    mime = payload.get("logo_mime") or None
    logo_data = None
    encoded = payload.get("logo_base64") or ""
    if encoded:
        if mime not in ("image/png", "image/jpeg"):
            raise ValueError("A marca deve ser um arquivo PNG ou JPG")
        try:
            logo_data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Arquivo de marca inválido") from exc
        if len(logo_data) > 2 * 1024 * 1024:
            raise ValueError("A marca deve ter até 2 MB")
        signature_ok = logo_data.startswith(b"\x89PNG\r\n\x1a\n") if mime == "image/png" else logo_data.startswith(b"\xff\xd8\xff")
        if not signature_ok:
            raise ValueError("O conteúdo do arquivo não corresponde ao formato informado")

    before = _company_row(connection)
    if encoded:
        connection.execute(
            """UPDATE configuracoes_empresa SET nome=?,cnpj=?,email=?,telefone=?,endereco=?,
               logo_mime=?,logo_dados=?,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=1""",
            (name, cnpj, email, phone, address, mime, logo_data, user_id),
        )
    elif payload.get("remover_logo"):
        connection.execute(
            """UPDATE configuracoes_empresa SET nome=?,cnpj=?,email=?,telefone=?,endereco=?,
               logo_mime=NULL,logo_dados=NULL,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=1""",
            (name, cnpj, email, phone, address, user_id),
        )
    else:
        connection.execute(
            """UPDATE configuracoes_empresa SET nome=?,cnpj=?,email=?,telefone=?,endereco=?,
               atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=1""",
            (name, cnpj, email, phone, address, user_id),
        )
    after = _company_row(connection)
    before_safe = {key: before[key] for key in ("nome", "cnpj", "email", "telefone", "endereco")}
    after_safe = {key: after[key] for key in before_safe}
    after_safe["tem_logo"] = bool(after["logo_dados"])
    audit(connection, "configuracoes_empresa", 1, "alterada", user_id, before_safe, after_safe, ip)
    for row in connection.execute("SELECT obra_id FROM configuracoes_obra"):
        sync_field_settings(connection, row["obra_id"], user_id)
    connection.commit()
    return after_safe


def update_work_settings(connection, work_id, payload, user_id, ip=None):
    before_row = connection.execute(
        "SELECT * FROM configuracoes_obra WHERE obra_id=?", (work_id,)
    ).fetchone()
    if not before_row:
        raise ValueError("Configuração da obra não encontrada")
    responsible = str(payload.get("responsavel_apontamento") or "").strip().upper()
    if len(responsible) > 180:
        raise ValueError("O nome do responsável deve ter até 180 caracteres")
    block_future = payload.get("bloquear_datas_futuras")
    if not isinstance(block_future, bool):
        raise ValueError("A regra de datas futuras deve ser marcada ou desmarcada")
    policy = payload.get("politica_coletivo", before_row["politica_coletivo"])
    if policy not in ("dividir_igualmente", "distribuicao_manual", "nao_importar"):
        raise ValueError("Regra de apontamento coletivo inválida")
    before = dict(before_row)
    connection.execute(
        """UPDATE configuracoes_obra SET responsavel_apontamento=?,bloquear_datas_futuras=?,
           politica_coletivo=?,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP WHERE obra_id=?""",
        (responsible, int(block_future), policy, user_id, work_id),
    )
    sync_field_settings(connection, work_id, user_id)
    after = dict(connection.execute("SELECT * FROM configuracoes_obra WHERE obra_id=?", (work_id,)).fetchone())
    audit(connection, "configuracoes_obra", work_id, "alterada", user_id, before, after, ip)
    connection.commit()
    return after


def update_preferences(connection, payload, user, ip=None):
    home = payload.get("tela_inicial", "overview")
    size = payload.get("linhas_por_pagina", 25)
    default_work = payload.get("obra_padrao_id")
    view_permissions = {"entries": "lancamentos", "quality": "qualidade", "pms": "pms", "payments": "pagamentos"}
    allowed_views = {"overview", *(view for view, permission in view_permissions.items() if database_module.has_permission(user, permission))}
    if home not in allowed_views:
        raise ValueError("Tela inicial inválida")
    if size not in (25, 50, 100):
        raise ValueError("Escolha 25, 50 ou 100 linhas por página")
    if default_work in (None, ""):
        default_work = None
    else:
        default_work = int(default_work)
        if not has_work_access(connection, user, default_work):
            raise PermissionError("Você não possui acesso à obra padrão escolhida")
    before_row = connection.execute("SELECT * FROM preferencias_usuario WHERE usuario_id=?", (user["id"],)).fetchone()
    before = dict(before_row) if before_row else None
    connection.execute(
        """INSERT INTO preferencias_usuario(usuario_id,obra_padrao_id,tela_inicial,linhas_por_pagina)
           VALUES(?,?,?,?) ON CONFLICT(usuario_id) DO UPDATE SET obra_padrao_id=excluded.obra_padrao_id,
           tela_inicial=excluded.tela_inicial,linhas_por_pagina=excluded.linhas_por_pagina,
           atualizado_em=CURRENT_TIMESTAMP""",
        (user["id"], default_work, home, size),
    )
    after = dict(connection.execute("SELECT * FROM preferencias_usuario WHERE usuario_id=?", (user["id"],)).fetchone())
    audit(connection, "preferencias_usuario", user["id"], "alteradas", user["id"], before, after, ip)
    connection.commit()
    return after


def field_link_suggestions(connection, work_id):
    pending_ids = {"employees": set(), "services": set()}
    for row in connection.execute(
        "SELECT id,data FROM apontamento_records WHERE obra_id=? AND kind='appointments'",
        (work_id,),
    ):
        appointment = json.loads(row["data"])
        if appointment.get("_deleted") or appointment.get("status") != "finalizado":
            continue
        if connection.execute(
            "SELECT 1 FROM lancamentos WHERE obra_id=? AND apontamento_origem_id=? LIMIT 1",
            (work_id, row["id"]),
        ).fetchone():
            continue
        pending_ids["employees"].update(str(value) for value in appointment.get("employeeIds", []))
        if appointment.get("serviceId"):
            pending_ids["services"].add(str(appointment["serviceId"]))
    snapshot = {}
    for kind in ("employees", "services"):
        ids = sorted(pending_ids[kind])
        if not ids:
            snapshot[kind] = []
            continue
        marks = ",".join("?" for _ in ids)
        snapshot[kind] = connection.execute(
            f"SELECT id,data FROM apontamento_records WHERE obra_id=? AND kind=? AND id IN ({marks}) ORDER BY updated_at DESC",
            [work_id, kind, *ids],
        ).fetchall()
    result = {"employees": [], "services": []}
    for kind, table, name_field in (("employees", "funcionarios", "name"), ("services", "servicos", "name")):
        for record in snapshot[kind]:
            source = json.loads(record["data"])
            if source.get("_deleted") or not source.get(name_field):
                continue
            explicit = connection.execute(
                "SELECT id_destino FROM apontamento_vinculos WHERE obra_id=? AND tipo_origem=? AND id_origem=?",
                (work_id, kind, record["id"]),
            ).fetchone()
            if kind == "employees":
                matches = connection.execute(
                    "SELECT id,nome FROM funcionarios WHERE obra_id=? AND ativo=1 ORDER BY nome",
                    (work_id,),
                ).fetchall()
            else:
                matches = connection.execute(
                    "SELECT id,nome_interno nome FROM servicos WHERE obra_id=? AND ativo=1 ORDER BY nome_interno",
                    (work_id,),
                ).fetchall()
            normalized = normalize_name(source[name_field])
            exact = [row for row in matches if normalize_name(row["nome"]) == normalized]
            target = explicit["id_destino"] if explicit else exact[0]["id"] if len(exact) == 1 else None
            result[kind].append({
                "id": record["id"], "nome": source[name_field], "destino_id": target,
                "sugestao_id": exact[0]["id"] if len(exact) == 1 else None,
                "opcoes": [{"id": row["id"], "nome": row["nome"]} for row in matches],
                "confianca": "confirmado" if explicit else "correspondência exata" if len(exact) == 1 else "selecione manualmente",
            })
    return result


def save_field_link(connection, work_id, kind, source_id, target_id, user_id, ip=None):
    if kind not in ("employees", "services"):
        raise ValueError("Tipo de vínculo inválido")
    source = _record(connection, work_id, kind, source_id)
    if not source or source.get("_deleted"):
        raise ValueError("Cadastro do apontamento não encontrado")
    table = "funcionarios" if kind == "employees" else "servicos"
    target = connection.execute(
        f"SELECT id FROM {table} WHERE id=? AND obra_id=? AND ativo=1", (int(target_id), work_id)
    ).fetchone()
    if not target:
        raise ValueError("Selecione um cadastro ativo da mesma obra")
    before = connection.execute(
        "SELECT * FROM apontamento_vinculos WHERE obra_id=? AND tipo_origem=? AND id_origem=?",
        (work_id, kind, str(source_id)),
    ).fetchone()
    connection.execute(
        """INSERT INTO apontamento_vinculos(obra_id,tipo_origem,id_origem,id_destino,criado_por)
           VALUES(?,?,?,?,?) ON CONFLICT(obra_id,tipo_origem,id_origem) DO UPDATE SET
           id_destino=excluded.id_destino,criado_por=excluded.criado_por,criado_em=CURRENT_TIMESTAMP""",
        (work_id, kind, str(source_id), int(target_id), user_id),
    )
    after = dict(connection.execute(
        "SELECT * FROM apontamento_vinculos WHERE obra_id=? AND tipo_origem=? AND id_origem=?",
        (work_id, kind, str(source_id)),
    ).fetchone())
    audit(connection, "apontamento_vinculo", after["id"], "criado" if not before else "alterado", user_id, dict(before) if before else None, after, ip)
    connection.commit()
    return after


def _field_location(connection, work_id, appointment):
    labels = []
    for location_id in appointment.get("locationIds", []):
        record = _record(connection, work_id, "locations", location_id)
        if record and not record.get("_deleted"):
            labels.append(str(record.get("label") or "").strip())
    compact = " · ".join(label for label in labels if label)
    casa = bloco = ap = None
    for label in labels:
        house = re.search(r"\b(?:casa|unidade)\s*([\w.-]+)", label, re.I)
        block = re.search(r"\bbloco\s*([\w.-]+)", label, re.I)
        apartment = re.search(r"\b(?:ap|apartamento)\s*([\w.-]+)", label, re.I)
        if house:
            casa = house.group(1)
        if block:
            bloco = block.group(1)
        if apartment:
            ap = apartment.group(1)
    return casa, bloco, ap, compact


def list_field_appointments(connection, work_id):
    employees = {
        row["id"]: json.loads(row["data"])
        for row in connection.execute("SELECT id,data FROM apontamento_records WHERE obra_id=? AND kind='employees'", (work_id,))
    }
    services = {
        row["id"]: json.loads(row["data"])
        for row in connection.execute("SELECT id,data FROM apontamento_records WHERE obra_id=? AND kind='services'", (work_id,))
    }
    result = []
    for row in connection.execute(
        "SELECT id,data FROM apontamento_records WHERE obra_id=? AND kind='appointments' ORDER BY updated_at DESC",
        (work_id,),
    ):
        appointment = json.loads(row["data"])
        if appointment.get("_deleted") or appointment.get("status") != "finalizado":
            continue
        casa, bloco, ap, local = _field_location(connection, work_id, appointment)
        existing = connection.execute(
            "SELECT COUNT(*) FROM lancamentos WHERE obra_id=? AND apontamento_origem_id=?",
            (work_id, row["id"]),
        ).fetchone()[0]
        result.append({
            "id": row["id"], "data": appointment.get("date"),
            "funcionarios": [employees.get(value, {}).get("name", "Cadastro ausente") for value in appointment.get("employeeIds", [])],
            "funcionario_ids": [str(value) for value in appointment.get("employeeIds", [])],
            "servico": services.get(appointment.get("serviceId"), {}).get("name", "Cadastro ausente"),
            "servico_id": str(appointment.get("serviceId") or ""),
            "local": local, "quantidade": appointment.get("quantity"), "unidade": appointment.get("unit", ""),
            "observacao": appointment.get("observation", ""), "importados": existing,
        })
    return result


def import_field_appointment(connection, work_id, appointment_id, user_id, ip=None):
    row = connection.execute(
        "SELECT data FROM apontamento_records WHERE obra_id=? AND kind='appointments' AND id=?",
        (work_id, str(appointment_id)),
    ).fetchone()
    if not row:
        raise ValueError("Apontamento de campo não encontrado nesta obra")
    appointment = json.loads(row["data"])
    if appointment.get("_deleted") or appointment.get("status") != "finalizado":
        raise ValueError("Somente apontamentos finalizados e ativos podem virar lançamento")
    validate_future_date(connection, work_id, appointment.get("date", ""))
    existing_count = connection.execute(
        "SELECT COUNT(*) FROM lancamentos WHERE obra_id=? AND apontamento_origem_id=?",
        (work_id, str(appointment_id)),
    ).fetchone()[0]
    if existing_count:
        raise ValueError("Este apontamento já foi importado para o sistema O gestor de Campo")
    employee_ids = list(dict.fromkeys(str(value) for value in appointment.get("employeeIds", [])))
    if not employee_ids:
        raise ValueError("O apontamento não contém colaboradores")
    service_source = _record(connection, work_id, "services", appointment.get("serviceId"))
    service_link = connection.execute(
        "SELECT id_destino FROM apontamento_vinculos WHERE obra_id=? AND tipo_origem='services' AND id_origem=?",
        (work_id, str(appointment.get("serviceId"))),
    ).fetchone()
    if not service_link and service_source:
        matches = connection.execute("SELECT id,nome_interno FROM servicos WHERE obra_id=? AND ativo=1", (work_id,)).fetchall()
        exact = [item for item in matches if normalize_name(item["nome_interno"]) == normalize_name(service_source.get("name"))]
        if len(exact) == 1:
            service_link = {"id_destino": exact[0]["id"]}
    if not service_link:
        raise ValueError("Vincule o serviço do campo a um serviço central antes de importar")
    service_id = int(service_link["id_destino"])
    employees = []
    for employee_source_id in employee_ids:
        source = _record(connection, work_id, "employees", employee_source_id)
        link = connection.execute(
            "SELECT id_destino FROM apontamento_vinculos WHERE obra_id=? AND tipo_origem='employees' AND id_origem=?",
            (work_id, employee_source_id),
        ).fetchone()
        if not link and source:
            matches = connection.execute("SELECT id,nome FROM funcionarios WHERE obra_id=? AND ativo=1", (work_id,)).fetchall()
            exact = [item for item in matches if normalize_name(item["nome"]) == normalize_name(source.get("name"))]
            if len(exact) == 1:
                link = {"id_destino": exact[0]["id"]}
        if not link:
            raise ValueError("Vincule cada colaborador do campo a um funcionário central antes de importar")
        employees.append((employee_source_id, int(link["id_destino"])))
    if len({item[1] for item in employees}) != len(employees):
        raise ValueError("Dois colaboradores do campo apontam para o mesmo cadastro central")
    settings = connection.execute(
        "SELECT politica_coletivo FROM configuracoes_obra WHERE obra_id=?", (work_id,)
    ).fetchone()
    if len(employees) > 1 and settings["politica_coletivo"] != "dividir_igualmente":
        raise ValueError("A configuração desta obra não autoriza importar apontamentos coletivos")
    raw_quantity = appointment.get("quantity")
    shares = [None] * len(employees)
    if raw_quantity is not None:
        total = Decimal(str(raw_quantity))
        if not total.is_finite() or total < 0 or total > Decimal("1000000000"):
            raise ValueError("A quantidade do apontamento está fora do limite permitido")
        quantum = Decimal("0.0001")
        if total.quantize(quantum) != total:
            raise ValueError("A quantidade deve ter no máximo quatro casas decimais para divisão exata")
        base = (total / len(employees)).quantize(quantum, rounding=ROUND_DOWN)
        shares = [base] * len(employees)
        shares[-1] = total - sum(shares[:-1], Decimal(0))
    casa, bloco, ap, local = _field_location(connection, work_id, appointment)
    note = str(appointment.get("observation") or "").strip()
    if local:
        note = f"{note}\nLocal de campo: {local}".strip()
    if len(note) > 2000:
        raise ValueError("A observação importada excede 2.000 caracteres")
    created = []
    for (source_employee_id, employee_id), share in zip(employees, shares):
        created.append(create_entry(connection, {
            "obra_id": work_id, "data": appointment["date"], "funcionario_id": employee_id,
            "tipo": "produção", "servico_id": service_id,
            "quantidade_m2": float(share) if share is not None else None,
            "horas_trabalhadas": None, "desconto": 0, "extra": 0,
            "casa": casa, "bloco": bloco, "ap": ap, "obs": note,
            "apontamento_origem_id": str(appointment_id),
            "funcionario_origem_id": source_employee_id,
        }, user_id, ip, commit=False))
    appointment["erpImportadaEm"] = now_iso()
    appointment["erpLancamentosIds"] = [entry["id"] for entry in created]
    appointment["updatedAt"] = now_iso()
    connection.execute(
        """UPDATE apontamento_records SET updated_at=?,data=?,atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP
           WHERE obra_id=? AND kind='appointments' AND id=?""",
        (appointment["updatedAt"], json.dumps(appointment, ensure_ascii=False), user_id, work_id, str(appointment_id)),
    )
    audit(connection, "apontamento_campo", None, "importado_para_lancamentos", user_id,
          after={"obra_id": work_id, "apontamento_id": str(appointment_id), "lancamentos": created}, ip=ip)
    connection.commit()
    return {"lancamentos": created, "quantidade_total": float(sum(shares, Decimal(0))) if raw_quantity is not None else None}


def validate_future_date(connection, work_id, value):
    row = connection.execute(
        "SELECT bloquear_datas_futuras FROM configuracoes_obra WHERE obra_id=?", (work_id,)
    ).fetchone()
    if row and row["bloquear_datas_futuras"] and value > date.today().isoformat():
        raise ValueError("A configuração desta obra bloqueia registros em datas futuras")


def update_account(connection, payload, user, ip=None):
    name = str(payload.get("nome") or "").strip().upper()
    email = str(payload.get("email") or "").strip()
    if not name or len(name) > 180:
        raise ValueError("Informe seu nome (até 180 caracteres)")
    if len(email) > 180 or (email and ("@" not in email or not email.split("@", 1)[0])):
        raise ValueError("Informe um e-mail válido")
    before = connection.execute(
        "SELECT id,nome,email FROM usuarios WHERE id=?", (user["id"],)
    ).fetchone()
    connection.execute(
        "UPDATE usuarios SET nome=?,email=?,versao=versao+1,atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
        (name, email or None, user["id"]),
    )
    after = dict(connection.execute(
        "SELECT id,nome,email FROM usuarios WHERE id=?", (user["id"],)
    ).fetchone())
    audit(connection, "usuario", user["id"], "dados_pessoais_alterados", user["id"], dict(before), after, ip)
    connection.commit()
    return after


def backup_database_bytes(connection):
    if database_module.using_postgres():
        raise ValueError("No PostgreSQL, use os backups automáticos do provedor do banco")
    target = sqlite3.connect(":memory:")
    try:
        connection.backup(target)
        return target.serialize()
    finally:
        target.close()


def restore_database(connection, raw, confirmation, user_id, ip=None):
    if database_module.using_postgres():
        raise ValueError("A restauração de arquivo SQLite não está disponível no PostgreSQL")
    if confirmation != "RESTAURAR":
        raise ValueError("Digite RESTAURAR para confirmar a substituição do banco")
    if not isinstance(raw, bytes) or not raw or len(raw) > 128 * 1024 * 1024:
        raise ValueError("O arquivo de backup está vazio ou excede 128 MB")
    backup_dir = Path(database_module.DB_PATH).parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stage_handle, stage_name = tempfile.mkstemp(prefix="pms-restore-upload-", suffix=".sqlite3", dir=backup_dir)
    os.close(stage_handle)
    stage_path = Path(stage_name)
    stage_path.write_bytes(raw)
    os.chmod(stage_path, 0o600)
    staged = sqlite3.connect(stage_path)
    try:
        try:
            integrity = staged.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise ValueError("Arquivo SQLite inválido ou corrompido") from exc
        if integrity != "ok":
            raise ValueError("O backup não passou na verificação de integridade")
        tables = {row[0] for row in staged.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"obras", "usuarios", "lancamentos", "schema_migrations", "configuracoes_obra"}
        if not required.issubset(tables):
            raise ValueError("O arquivo não é um backup compatível com o sistema O gestor de Campo")
        versions = {row[0] for row in staged.execute("SELECT version FROM schema_migrations")}
        if "0004" not in versions:
            raise ValueError("O backup é antigo. Atualize-o no sistema O gestor de Campo antes de restaurar")
        columns = {row[1] for row in staged.execute("PRAGMA table_info(lancamentos)")}
        if not {"apontamento_origem_id", "funcionario_origem_id"}.issubset(columns):
            raise ValueError("O backup não contém o vínculo de apontamento exigido pelo sistema O gestor de Campo")

        handle, backup_name = tempfile.mkstemp(prefix="pms-pre-restauracao-", suffix=".sqlite3", dir=backup_dir)
        os.close(handle)
        backup_path = Path(backup_name)
        try:
            saved = sqlite3.connect(backup_path)
            try:
                connection.backup(saved)
            finally:
                saved.close()
            os.chmod(backup_path, 0o600)
            connection.commit()
            staged.backup(connection)
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError("A base restaurada falhou na verificação de integridade")
        except Exception:
            saved = sqlite3.connect(backup_path)
            try:
                saved.backup(connection)
                connection.commit()
            finally:
                saved.close()
            raise
        actor = connection.execute("SELECT 1 FROM usuarios WHERE id=?", (user_id,)).fetchone()
        audit(connection, "banco", None, "restaurado", user_id if actor else None,
              after={"backup_automatico": backup_path.name, "iniciado_por_usuario_id": user_id}, ip=ip)
        connection.commit()
        return {"ok": True, "backup_automatico": backup_path.name}
    finally:
        staged.close()
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{stage_path}{suffix}").unlink(missing_ok=True)
