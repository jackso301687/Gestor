"""Persistência e regras da proposta de medição."""
from __future__ import annotations

from datetime import date
import re

from database import audit


def _text(value):
    return str(value or "").strip()


def _natural(value):
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", _text(value))]


def _proposal(connection, proposal_id):
    row = connection.execute(
        """SELECT p.*,o.nome obra FROM propostas_medicao p
           JOIN obras o ON o.id=p.obra_id WHERE p.id=?""", (proposal_id,)
    ).fetchone()
    if not row:
        raise ValueError("Proposta de medição não encontrada")
    return row


def proposal_data(connection, proposal_id):
    proposal = dict(_proposal(connection, proposal_id))
    services = [dict(row) for row in connection.execute(
        "SELECT * FROM proposta_servicos WHERE proposta_id=? ORDER BY ordem,id", (proposal_id,)
    )]
    for service in services:
        service["itens"] = [dict(row) for row in connection.execute(
            "SELECT * FROM proposta_itens WHERE proposta_servico_id=? ORDER BY ordem,id", (service["id"],)
        )]
    proposal["servicos"] = services
    return proposal


def list_proposals(connection, obra_id):
    return [dict(row) for row in connection.execute(
        """SELECT p.*,COUNT(s.id) servicos,COUNT(i.id) itens
           FROM propostas_medicao p LEFT JOIN proposta_servicos s ON s.proposta_id=p.id
           LEFT JOIN proposta_itens i ON i.proposta_servico_id=s.id
           WHERE p.obra_id=? GROUP BY p.id ORDER BY p.atualizado_em DESC,p.id DESC""", (obra_id,)
    )]


def create_from_entries(connection, obra_id, periodo_inicio, periodo_fim, data_proposta, pms_numero, user_id, ip):
    try:
        date.fromisoformat(periodo_inicio); date.fromisoformat(periodo_fim); date.fromisoformat(data_proposta)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe período e data da proposta no formato AAAA-MM-DD") from exc
    if periodo_inicio > periodo_fim:
        raise ValueError("O início do período não pode ser posterior ao fim")
    raw = connection.execute(
        """SELECT l.servico_id,s.nome_interno nome_servico,
                  l.bloco,COALESCE(NULLIF(TRIM(l.casa),''),NULLIF(TRIM(l.ap),''),'') casa
           FROM lancamentos l JOIN servicos s ON s.id=l.servico_id
           WHERE l.obra_id=? AND l.ativo=1 AND l.data BETWEEN ? AND ? AND LOWER(TRIM(COALESCE(l.tipo,'')))='produção'
           ORDER BY nome_servico,l.bloco,l.casa,l.id""", (obra_id, periodo_inicio, periodo_fim)
    ).fetchall()
    grouped = {}
    for row in raw:
        name, block, house = _text(row["nome_servico"]), _text(row["bloco"]), _text(row["casa"])
        key = name.casefold()
        service = grouped.setdefault(key, {"id": row["servico_id"], "nome": name, "items": {}})
        service["items"].setdefault((block.casefold(), house.casefold()), {"bloco": block, "casa": house})
    cursor = connection.execute(
        """INSERT INTO propostas_medicao(obra_id,pms_numero,periodo_inicio,periodo_fim,data_proposta,criado_por)
           VALUES(?,?,?,?,?,?)""", (obra_id, pms_numero or None, periodo_inicio, periodo_fim, data_proposta, user_id)
    )
    proposal_id = cursor.lastrowid
    for service_order, service in enumerate(sorted(grouped.values(), key=lambda value: _natural(value["nome"])), 1):
        service_id = connection.execute(
            "INSERT INTO proposta_servicos(proposta_id,servico_id,nome_servico,ordem) VALUES(?,?,?,?)",
            (proposal_id, service["id"], service["nome"], service_order),
        ).lastrowid
        items = sorted(service["items"].values(), key=lambda value: (_natural(value["bloco"]), _natural(value["casa"])))
        for item_order, item in enumerate(items, 1):
            connection.execute("INSERT INTO proposta_itens(proposta_servico_id,bloco,casa,ordem) VALUES(?,?,?,?)",
                               (service_id, item["bloco"] or None, item["casa"], item_order))
    audit(connection, "proposta_medicao", proposal_id, "gerada", user_id,
          after={"obra_id": obra_id, "periodo_inicio": periodo_inicio, "periodo_fim": periodo_fim, "itens_origem": len(raw)}, ip=ip)
    connection.commit()
    return proposal_data(connection, proposal_id)


def _touch(connection, proposal_id):
    connection.execute("UPDATE propostas_medicao SET atualizado_em=CURRENT_TIMESTAMP WHERE id=?", (proposal_id,))


def add_service(connection, proposal_id, nome_servico, user_id, ip):
    name = _text(nome_servico)
    if not name:
        raise ValueError("Informe o nome do serviço")
    _proposal(connection, proposal_id)
    order = connection.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM proposta_servicos WHERE proposta_id=?", (proposal_id,)).fetchone()[0]
    service_id = connection.execute("INSERT INTO proposta_servicos(proposta_id,nome_servico,ordem) VALUES(?,?,?)", (proposal_id,name,order)).lastrowid
    connection.execute("INSERT INTO proposta_itens(proposta_servico_id,casa,ordem) VALUES(?,?,1)", (service_id,""))
    _touch(connection, proposal_id); audit(connection,"proposta_servico",service_id,"criado",user_id,after={"nome_servico":name},ip=ip); connection.commit()
    return proposal_data(connection, proposal_id)


def add_item(connection, proposal_id, service_id, bloco, casa, user_id, ip):
    if not connection.execute("SELECT 1 FROM proposta_servicos WHERE id=? AND proposta_id=?", (service_id,proposal_id)).fetchone():
        raise ValueError("Grupo de serviço não pertence à proposta")
    order = connection.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM proposta_itens WHERE proposta_servico_id=?", (service_id,)).fetchone()[0]
    item_id = connection.execute("INSERT INTO proposta_itens(proposta_servico_id,bloco,casa,ordem) VALUES(?,?,?,?)", (service_id,_text(bloco) or None,_text(casa),order)).lastrowid
    _touch(connection, proposal_id); audit(connection,"proposta_item",item_id,"criado",user_id,after={"bloco":_text(bloco),"casa":_text(casa)},ip=ip); connection.commit()
    return proposal_data(connection, proposal_id)


def update_service(connection, proposal_id, service_id, payload, user_id, ip):
    row = connection.execute("SELECT * FROM proposta_servicos WHERE id=? AND proposta_id=?", (service_id,proposal_id)).fetchone()
    if not row: raise ValueError("Grupo de serviço não encontrado")
    name = _text(payload.get("nome_servico", row["nome_servico"]))
    if not name: raise ValueError("Informe o nome do serviço")
    connection.execute("UPDATE proposta_servicos SET nome_servico=? WHERE id=?", (name,service_id)); _touch(connection,proposal_id)
    audit(connection,"proposta_servico",service_id,"alterado",user_id,before=dict(row),after={"nome_servico":name},ip=ip); connection.commit(); return proposal_data(connection,proposal_id)


def update_item(connection, proposal_id, item_id, payload, user_id, ip):
    row = connection.execute("""SELECT i.* FROM proposta_itens i JOIN proposta_servicos s ON s.id=i.proposta_servico_id
                                WHERE i.id=? AND s.proposta_id=?""", (item_id,proposal_id)).fetchone()
    if not row: raise ValueError("Item da proposta não encontrado")
    bloco, casa = _text(payload.get("bloco",row["bloco"])), _text(payload.get("casa",row["casa"]))
    connection.execute("UPDATE proposta_itens SET bloco=?,casa=? WHERE id=?", (bloco or None,casa,item_id)); _touch(connection,proposal_id)
    audit(connection,"proposta_item",item_id,"alterado",user_id,before=dict(row),after={"bloco":bloco,"casa":casa},ip=ip); connection.commit(); return proposal_data(connection,proposal_id)


def update_block(connection, proposal_id, item_ids, block, user_id, ip):
    _proposal(connection, proposal_id)
    try: requested = list(dict.fromkeys(int(value) for value in item_ids))
    except (TypeError, ValueError) as exc: raise ValueError("Itens do bloco inválidos") from exc
    if not requested: raise ValueError("Selecione ao menos uma casa do bloco")
    placeholders=",".join("?" for _ in requested)
    rows=connection.execute(
        f"""SELECT i.*,s.id service_id FROM proposta_itens i JOIN proposta_servicos s ON s.id=i.proposta_servico_id
            WHERE i.id IN ({placeholders}) AND s.proposta_id=? ORDER BY i.ordem""", (*requested,proposal_id)
    ).fetchall()
    if len(rows)!=len(requested) or len({row["service_id"] for row in rows})!=1 or len({row["bloco"] or "" for row in rows})!=1:
        raise ValueError("As casas informadas não formam um único grupo de bloco")
    value=_text(block) or None
    connection.execute(f"UPDATE proposta_itens SET bloco=? WHERE id IN ({placeholders})", (value,*requested))
    _touch(connection,proposal_id)
    audit(connection,"proposta_item",requested[0],"bloco_alterado",user_id,
          before={"itens":[dict(row) for row in rows]},after={"item_ids":requested,"bloco":value},ip=ip)
    connection.commit(); return proposal_data(connection,proposal_id)


def delete_item(connection, proposal_id, item_id, user_id, ip):
    row = connection.execute("""SELECT i.*,s.id service_id FROM proposta_itens i JOIN proposta_servicos s ON s.id=i.proposta_servico_id
                                WHERE i.id=? AND s.proposta_id=?""", (item_id,proposal_id)).fetchone()
    if not row: raise ValueError("Item da proposta não encontrado")
    remaining = connection.execute("SELECT COUNT(*) FROM proposta_itens WHERE proposta_servico_id=?", (row["proposta_servico_id"],)).fetchone()[0]
    if remaining == 1:
        connection.execute("UPDATE proposta_itens SET bloco=NULL,casa='' WHERE id=?",(item_id,))
    else:
        connection.execute("DELETE FROM proposta_itens WHERE id=?",(item_id,))
    _touch(connection,proposal_id)
    audit(connection,"proposta_item",item_id,"removido",user_id,before=dict(row),ip=ip); connection.commit(); return proposal_data(connection,proposal_id)


def delete_service(connection, proposal_id, service_id, user_id, ip):
    row=connection.execute("SELECT * FROM proposta_servicos WHERE id=? AND proposta_id=?",(service_id,proposal_id)).fetchone()
    if not row: raise ValueError("Grupo de serviço não encontrado")
    connection.execute("DELETE FROM proposta_servicos WHERE id=?",(service_id,)); _touch(connection,proposal_id)
    audit(connection,"proposta_servico",service_id,"removido",user_id,before=dict(row),ip=ip); connection.commit(); return proposal_data(connection,proposal_id)


def reorder_services(connection, proposal_id, service_ids, user_id, ip):
    _proposal(connection, proposal_id)
    current = [row["id"] for row in connection.execute("SELECT id FROM proposta_servicos WHERE proposta_id=? ORDER BY ordem,id", (proposal_id,))]
    try: requested = [int(value) for value in service_ids]
    except (TypeError, ValueError) as exc: raise ValueError("Ordem dos serviços inválida") from exc
    if len(requested) != len(current) or set(requested) != set(current):
        raise ValueError("A nova ordem deve conter todos os serviços da proposta uma única vez")
    connection.execute("UPDATE proposta_servicos SET ordem=-id WHERE proposta_id=?", (proposal_id,))
    for order, service_id in enumerate(requested, 1):
        connection.execute("UPDATE proposta_servicos SET ordem=? WHERE id=? AND proposta_id=?", (order,service_id,proposal_id))
    _touch(connection,proposal_id)
    audit(connection,"proposta_medicao",proposal_id,"servicos_reordenados",user_id,before={"ordem":current},after={"ordem":requested},ip=ip)
    connection.commit(); return proposal_data(connection,proposal_id)
