"""Geração e edição de propostas de pagamento sem efetivar quitação."""
from __future__ import annotations

from datetime import date
import json
import math

from database import audit


def _non_negative(value, field):
    try:
        parsed=float(value or 0)
    except (TypeError,ValueError) as exc:
        raise ValueError(f"{field} precisa ser numérico") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} não pode ser negativo ou inválido")
    return parsed


def _header(connection, proposal_id):
    row = connection.execute(
        "SELECT p.*,o.nome obra FROM propostas_pagamento p JOIN obras o ON o.id=p.obra_id WHERE p.id=?",
        (proposal_id,),
    ).fetchone()
    if not row:
        raise ValueError("Proposta de pagamento não encontrada")
    return row


def proposal_data(connection, proposal_id):
    result = dict(_header(connection, proposal_id))
    result["itens"] = [dict(row) for row in connection.execute(
        """SELECT i.*,f.nome funcionario,f.profissao,s.nome_interno servico
           FROM proposta_pagamento_itens i JOIN funcionarios f ON f.id=i.funcionario_id
           JOIN servicos s ON s.id=i.servico_id
           WHERE i.proposta_id=? AND i.ativo=1
           ORDER BY f.nome,s.nome_interno,i.bloco,i.ap,i.casa,i.id""", (proposal_id,),
    )]
    result["totais"] = {
        "valor_gerado": round(sum(float(row["valor_gerado"] or 0) for row in result["itens"]), 2),
        "valor_pagar": round(sum(float(row["valor_pagar"] or 0) for row in result["itens"]), 2),
        "funcionarios": len({row["funcionario_id"] for row in result["itens"]}),
    }
    return result


def list_proposals(connection, obra_id, start=None, end=None):
    where, params = ["p.obra_id=?"], [obra_id]
    if start:
        where.append("p.periodo_fim>=?"); params.append(start)
    if end:
        where.append("p.periodo_inicio<=?"); params.append(end)
    return [dict(row) for row in connection.execute(
        f"""SELECT p.*,COUNT(i.id) itens,ROUND(COALESCE(SUM(i.valor_gerado),0),2) valor_gerado,
                   ROUND(COALESCE(SUM(i.valor_pagar),0),2) valor_pagar
            FROM propostas_pagamento p LEFT JOIN proposta_pagamento_itens i
              ON i.proposta_id=p.id AND i.ativo=1
            WHERE {' AND '.join(where)} GROUP BY p.id
            ORDER BY p.atualizado_em DESC,p.id DESC""", params,
    )]


def create_from_entries(connection, work_id, start, end, pms_number, user_id, ip):
    try:
        date.fromisoformat(start); date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe o período no formato AAAA-MM-DD") from exc
    if start > end:
        raise ValueError("O início do período não pode ser posterior ao fim")
    header = connection.execute(
        """INSERT INTO propostas_pagamento(obra_id,pms_numero,periodo_inicio,periodo_fim,criado_por)
           VALUES(?,?,?,?,?) RETURNING id""", (work_id,pms_number or None,start,end,user_id),
    ).fetchone()
    proposal_id = header["id"]
    rows = connection.execute(
        """SELECT l.funcionario_id,l.servico_id,COALESCE(l.bloco,'') bloco,COALESCE(l.ap,'') ap,
                  COALESCE(l.casa,'') casa,
                  SUM(COALESCE(l.quantidade_m2,l.horas_trabalhadas,1)) quantidade,
                  SUM(l.valor_receber) valor_gerado,SUM(l.valor_pagar) valor_pagar,
                  json_group_array(l.id) origem_lancamentos
           FROM lancamentos l
           WHERE l.obra_id=? AND l.ativo=1 AND l.data BETWEEN ? AND ?
             AND LOWER(TRIM(COALESCE(l.tipo,'')))='produção'
             AND (? IS NULL OR l.pms_numero=? OR l.pms_numero IS NULL)
           GROUP BY l.funcionario_id,l.servico_id,COALESCE(l.bloco,''),COALESCE(l.ap,''),COALESCE(l.casa,'')
           ORDER BY l.funcionario_id,l.servico_id,bloco,ap,casa""",
        (work_id,start,end,pms_number,pms_number),
    ).fetchall()
    for row in rows:
        connection.execute(
            """INSERT INTO proposta_pagamento_itens
               (proposta_id,funcionario_id,servico_id,bloco,ap,casa,quantidade,valor_gerado,valor_pagar,origem_lancamentos)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (proposal_id,row["funcionario_id"],row["servico_id"],row["bloco"],row["ap"],row["casa"],
             row["quantidade"] or 0,row["valor_gerado"] or 0,row["valor_pagar"] or 0,row["origem_lancamentos"]),
        )
    audit(connection,"proposta_pagamento",proposal_id,"gerada",user_id,
          after={"obra_id":work_id,"periodo_inicio":start,"periodo_fim":end,"itens_origem":len(rows)},ip=ip)
    connection.commit()
    return proposal_data(connection, proposal_id)


def add_item(connection, proposal_id, payload, user_id, ip):
    header = _header(connection, proposal_id)
    employee_id, service_id = int(payload.get("funcionario_id") or 0), int(payload.get("servico_id") or 0)
    for table, row_id, label in (("funcionarios",employee_id,"Funcionário"),("servicos",service_id,"Serviço")):
        row = connection.execute(f"SELECT obra_id,ativo FROM {table} WHERE id=?", (row_id,)).fetchone()
        if not row or row["obra_id"] != header["obra_id"] or not row["ativo"]:
            raise ValueError(f"{label} inválido ou inativo para esta obra")
    quantity = _non_negative(payload.get("quantidade"),"Quantidade")
    generated = _non_negative(payload.get("valor_gerado"),"Valor gerado")
    payable = _non_negative(payload.get("valor_pagar"),"Valor a pagar")
    fields = ("bloco","ap","casa","observacao")
    values = [str(payload.get(key) or "").strip()[:500] for key in fields]
    cursor = connection.execute(
        """INSERT INTO proposta_pagamento_itens(proposta_id,funcionario_id,servico_id,bloco,ap,casa,quantidade,valor_gerado,valor_pagar,observacao)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (proposal_id,employee_id,service_id,*values[:3],quantity,generated,payable,values[3]),
    )
    connection.execute("UPDATE propostas_pagamento SET atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(proposal_id,))
    audit(connection,"proposta_pagamento_item",cursor.lastrowid,"adicionado",user_id,after=payload,ip=ip)
    connection.commit()
    return proposal_data(connection,proposal_id)


def update_item(connection, proposal_id, item_id, payload, user_id, ip):
    row = connection.execute("SELECT * FROM proposta_pagamento_itens WHERE id=? AND proposta_id=? AND ativo=1",(item_id,proposal_id)).fetchone()
    if not row:
        raise ValueError("Item da proposta não encontrado")
    allowed = ("funcionario_id","servico_id","bloco","ap","casa","quantidade","valor_gerado","valor_pagar","observacao")
    merged = dict(row)
    merged.update({key:payload[key] for key in allowed if key in payload})
    if "funcionario_id" in payload or "servico_id" in payload:
        for table,key,label in (("funcionarios","funcionario_id","Funcionário"),("servicos","servico_id","Serviço")):
            found=connection.execute(f"SELECT obra_id,ativo FROM {table} WHERE id=?",(int(merged[key]),)).fetchone()
            if not found or found["obra_id"]!=_header(connection,proposal_id)["obra_id"] or not found["ativo"]:
                raise ValueError(f"{label} inválido ou inativo para esta obra")
    for key,label in (("quantidade","Quantidade"),("valor_gerado","Valor gerado"),("valor_pagar","Valor a pagar")):
        merged[key]=_non_negative(merged[key],label)
    for key in ("bloco","ap","casa","observacao"):
        merged[key]=str(merged[key] or "").strip()[:500]
    connection.execute(
        """UPDATE proposta_pagamento_itens SET funcionario_id=?,servico_id=?,bloco=?,ap=?,casa=?,quantidade=?,
           valor_gerado=?,valor_pagar=?,observacao=?,atualizado_em=CURRENT_TIMESTAMP WHERE id=?""",
        tuple(merged[key] for key in ("funcionario_id","servico_id","bloco","ap","casa","quantidade","valor_gerado","valor_pagar","observacao"))+(item_id,),
    )
    connection.execute("UPDATE propostas_pagamento SET atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(proposal_id,))
    audit(connection,"proposta_pagamento_item",item_id,"alterado",user_id,before=dict(row),after=merged,ip=ip)
    connection.commit()
    return proposal_data(connection,proposal_id)


def remove_item(connection, proposal_id, item_id, user_id, ip):
    row=connection.execute("SELECT * FROM proposta_pagamento_itens WHERE id=? AND proposta_id=? AND ativo=1",(item_id,proposal_id)).fetchone()
    if not row: raise ValueError("Item da proposta não encontrado")
    connection.execute("UPDATE proposta_pagamento_itens SET ativo=0,atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(item_id,))
    connection.execute("UPDATE propostas_pagamento SET atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(proposal_id,))
    audit(connection,"proposta_pagamento_item",item_id,"removido",user_id,before=dict(row),ip=ip)
    connection.commit()
    return proposal_data(connection,proposal_id)
