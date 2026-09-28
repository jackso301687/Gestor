"""Consultas e documentos PMS em PDF/HTML no padrão da planilha de referência."""
from __future__ import annotations

import html
import io
import zipfile
import base64
import textwrap
from datetime import date


REPORT_TYPES = {"dados", "resumo", "pms", "apropriacao", "pagamento", "reembolso"}


def _filters(query):
    filters = {
        "obra_id": int(query.get("obra_id", [1])[0]),
        "start": query.get("start", [None])[0],
        "end": query.get("end", [None])[0],
        "funcionario_id": query.get("funcionario_id", [None])[0],
        "servico_id": query.get("servico_id", [None])[0],
        "profissao": query.get("profissao", [None])[0],
        "pms": query.get("pms", [None])[0],
        "casa": query.get("casa", [None])[0],
        "bloco": query.get("bloco", [None])[0],
        "ap": query.get("ap", [None])[0],
        "qualidade": query.get("qualidade", [None])[0],
    }
    try:
        start_date = date.fromisoformat(filters["start"]) if filters["start"] else None
        end_date = date.fromisoformat(filters["end"]) if filters["end"] else None
    except (TypeError, ValueError) as exc:
        raise ValueError("Período inválido. Informe datas no formato AAAA-MM-DD.") from exc
    if start_date and end_date and start_date > end_date:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    return filters


def report_data(connection, report_type, query):
    if report_type not in REPORT_TYPES:
        raise ValueError("Relatório inválido")
    f = _filters(query)
    obra = connection.execute("SELECT * FROM obras WHERE id=?", (f["obra_id"],)).fetchone()
    if not obra:
        raise ValueError("Obra não encontrada")
    if report_type == "apropriacao":
        where, params = ["a.obra_id=?"], [f["obra_id"]]
        if f["start"]:
            where.append("a.data>=?"); params.append(f["start"])
        if f["end"]:
            where.append("a.data<=?"); params.append(f["end"])
        if f["funcionario_id"]:
            where.append("a.funcionario_id=?"); params.append(f["funcionario_id"])
        if f["profissao"]:
            where.append("fn.profissao=?"); params.append(f["profissao"])
        if f["servico_id"]:
            where.append("a.servico_id=?"); params.append(f["servico_id"])
        rows = connection.execute(
            f"""SELECT a.data,fn.nome funcionario,fn.profissao,s.nome_interno servico,
                       a.inicio_manha,a.termino_manha,a.inicio_tarde,a.termino_tarde,
                       a.assinatura_encarregado,a.pms_numero,a.observacao
                FROM apropriacoes a JOIN funcionarios fn ON fn.id=a.funcionario_id
                JOIN servicos s ON s.id=a.servico_id
                WHERE {' AND '.join(where)} ORDER BY a.data,fn.nome,a.id""", params,
        ).fetchall()
        columns = [
            ("DATA","data"),("FUNCIONÁRIO","funcionario"),("PROFISSÃO","profissao"),
            ("SERVIÇO","servico"),("INÍCIO","inicio_manha"),("TÉRMINO","termino_manha"),
            ("INÍCIO","inicio_tarde"),("TÉRMINO","termino_tarde"),
            ("ASSINATURA DO ENCARREGADO","assinatura_encarregado"),
        ]
        title = "Apropriações"
    elif report_type == "pms":
        where, params = ["l.obra_id=?", "l.ativo=1", "l.valor_receber>0"], [f["obra_id"]]
        if f["start"]:
            where.append("l.data>=?"); params.append(f["start"])
        if f["end"]:
            where.append("l.data<=?"); params.append(f["end"])
        if f["funcionario_id"]:
            where.append("l.funcionario_id=?"); params.append(f["funcionario_id"])
        if f["servico_id"]:
            where.append("l.servico_id=?"); params.append(f["servico_id"])
        if f["pms"]:
            where.append("COALESCE(l.pms_numero,?)=?"); params.extend([obra["pms_atual"], f["pms"]])
        if f["qualidade"] and f["qualidade"] != "todos":
            where.append("COALESCE(q.situacao,'nao_avaliado')=?"); params.append(f["qualidade"])
        rows = connection.execute(
            f"""SELECT s.descricao_pms servico_pms,s.unidade,
                       ROUND(SUM(COALESCE(l.quantidade_m2,1)),4) quantidade,
                       l.casa,l.bloco,l.ap,GROUP_CONCAT(DISTINCT l.obs) obs,
                       CASE WHEN MIN(COALESCE(q.situacao,'nao_avaliado'))='ok' THEN 'OK'
                            WHEN MAX(COALESCE(q.situacao,'nao_avaliado'))='pendente' THEN 'PENDENTE'
                            ELSE 'NÃO AVALIADO' END ok,
                       GROUP_CONCAT(DISTINCT q.observacao) observacao_qualidade,
                       ROUND(SUM(l.valor_receber),2) valor_receber
                FROM lancamentos l JOIN servicos s ON s.id=l.servico_id
                LEFT JOIN avaliacoes_qualidade q ON q.lancamento_id=l.id
                WHERE {' AND '.join(where)}
                GROUP BY s.id,l.casa,l.bloco,l.ap ORDER BY s.descricao_pms,l.bloco,l.ap,l.casa""", params,
        ).fetchall()
        columns = [
            ("SERVIÇO PMS","servico_pms"),("QTD.","quantidade"),("CASA","casa"),
            ("BLOCO","bloco"),("AP","ap"),("QUALIDADE","ok"),
            ("A RECEBER (R$)","valor_receber"),
        ]
        title = "PRODUÇÃO ELEGÍVEL PARA MEDIÇÃO"
    elif report_type == "pagamento":
        closing_id = query.get("fechamento_id", [None])[0]
        where, params = ["l.obra_id=?", "l.ativo=1"], [f["obra_id"]]
        joins = ""
        select_adjusted = "NULL valor_ajustado,l.valor_pagar total_pagar"
        if closing_id:
            closing = connection.execute("SELECT * FROM fechamentos_pagamento WHERE id=? AND obra_id=?", (closing_id, f["obra_id"])).fetchone()
            if not closing:
                raise ValueError("Fechamento não encontrado nesta obra")
            f["start"], f["end"] = closing["periodo_inicio"], closing["periodo_fim"]
            f["pms"] = closing["pms_numero"]
            joins = "JOIN itens_pagamento i ON i.lancamento_id=l.id AND i.fechamento_id=?"
            params = [closing_id, *params]
            select_adjusted = "i.valor_ajustado,i.valor_final total_pagar"
        if f["start"]:
            where.append("l.data>=?"); params.append(f["start"])
        if f["end"]:
            where.append("l.data<=?"); params.append(f["end"])
        rows = connection.execute(
            f"""SELECT fn.nome funcionario,fn.profissao,s.nome_interno servico,l.casa,l.bloco,l.ap,
                       COALESCE(l.quantidade_m2,l.horas_trabalhadas,1) quantidade,
                       ROUND(l.valor_pagar-COALESCE(l.extra,0)+COALESCE(l.desconto,0),2) valor_padrao,
                       {select_adjusted},l.extra,l.desconto,l.valor_receber,
                       TRIM((CASE WHEN COALESCE(l.bloco,'')<>'' THEN 'Bl. '||l.bloco||' ' ELSE '' END) ||
                            (CASE WHEN COALESCE(l.ap,'')<>'' THEN 'AP '||l.ap||' ' ELSE '' END) ||
                            (CASE WHEN COALESCE(l.casa,'')<>'' THEN 'Casa '||l.casa ELSE '' END)) local
                FROM lancamentos l {joins} JOIN funcionarios fn ON fn.id=l.funcionario_id
                JOIN servicos s ON s.id=l.servico_id WHERE {' AND '.join(where)}
                ORDER BY fn.nome,s.nome_interno,l.data""", params,
        ).fetchall()
        columns = [
            ("FUNCIONÁRIO","funcionario"),("SERVIÇO","servico"),("BLOCO / AP / CASA","local"),
            ("QTD.","quantidade"),("BASE (R$)","valor_padrao"),
            ("EXTRA LANÇ. (R$)","extra"),("DESCONTO LANÇ. (R$)","desconto"),
            (("FECHADO (R$)" if closing_id else "A PAGAR CALCULADO (R$)"),"total_pagar"),
        ]
        title = f"FECHAMENTO FINANCEIRO #{closing_id}" if closing_id else "CUSTOS CALCULADOS DA PRODUÇÃO"
    elif report_type == "reembolso":
        where, params = ["r.obra_id=?"], [f["obra_id"]]
        if f["start"]:
            where.append("r.data>=?"); params.append(f["start"])
        if f["end"]:
            where.append("r.data<=?"); params.append(f["end"])
        rows = connection.execute(
            f"""SELECT r.data,r.pms_numero,COALESCE(fn.nome,'—') funcionario,r.descricao,r.valor,r.status
               FROM reembolsos r LEFT JOIN funcionarios fn ON fn.id=r.funcionario_id
               WHERE {' AND '.join(where)} ORDER BY r.data,r.id""", params,
        ).fetchall()
        columns = [("DATA","data"),("PMS","pms_numero"),("FUNCIONÁRIO","funcionario"),("DESCRIÇÃO","descricao"),("VALOR","valor"),("STATUS","status")]
        title = "REEMBOLSOS"
    else:
        where, params = ["l.obra_id=?", "l.ativo=1"], [f["obra_id"]]
        if f["start"]:
            where.append("l.data>=?"); params.append(f["start"])
        if f["end"]:
            where.append("l.data<=?"); params.append(f["end"])
        if f["funcionario_id"]:
            where.append("l.funcionario_id=?"); params.append(f["funcionario_id"])
        if f["servico_id"]:
            where.append("l.servico_id=?"); params.append(f["servico_id"])
        rows = connection.execute(
            f"""SELECT l.data,fn.nome funcionario,fn.profissao,l.tipo,s.nome_interno servico,
                       s.descricao_pms servico_pms,l.quantidade_m2,l.horas_trabalhadas,
                       l.casa,l.bloco,l.ap,l.obs,l.extra,l.desconto,l.valor_pagar,l.valor_receber
                FROM lancamentos l JOIN funcionarios fn ON fn.id=l.funcionario_id
                JOIN servicos s ON s.id=l.servico_id WHERE {' AND '.join(where)}
                ORDER BY l.data,fn.nome,l.id""", params,
        ).fetchall()
        columns = [
            ("DATA","data"),("FUNCIONÁRIO","funcionario"),("PROFISSÃO","profissao"),
            ("TIPO","tipo"),("SERVIÇO","servico"),("CASA","casa"),("BLOCO","bloco"),
            ("AP","ap"),("QUANT.","quantidade_m2"),("EXTRA","extra"),
            ("DESCONTO","desconto"),("VALOR","valor_pagar"),
        ]
        title = "RESUMO OPERACIONAL" if report_type == "resumo" else "RELATÓRIO DE DADOS"
    data_rows = [{key: row[key] for _, key in columns} for row in rows]
    totals = {
        "linhas": len(data_rows),
        "pagar": round(sum(float(dict(row).get("total_pagar", dict(row).get("valor_pagar", 0)) or 0) for row in rows), 2),
        "receber": round(sum(float(dict(row).get("valor_receber", 0) or 0) for row in rows), 2),
    }
    overview = None
    if report_type == "pagamento" and closing_id:
        totals["pagar"] = round(float(closing["total_pagar"] or 0), 2)
        totals["receber"] = round(float(closing["total_receber"] or 0), 2)
        overview = [
            ("Serviços após ajustes", closing["total_servicos"]),
            ("Extras adicionais do fechamento", closing["total_extras"]),
            ("Descontos adicionais do fechamento", closing["total_descontos"]),
            ("Total registrado a pagar", closing["total_pagar"]),
            ("Produzido a receber", closing["total_receber"]),
            ("Margem calculada", round(float(closing["total_receber"] or 0)-float(closing["total_pagar"] or 0), 2)),
        ]
    company = connection.execute("SELECT nome,cnpj,email,telefone,endereco,logo_mime,logo_dados FROM configuracoes_empresa WHERE id=1").fetchone()
    brand = dict(company) if company else {"nome": ""}
    logo_data = brand.pop("logo_dados", None)
    brand["logo_base64"] = base64.b64encode(logo_data).decode("ascii") if logo_data else ""
    return {"type": report_type, "title": title, "obra": dict(obra), "company": brand,
            "columns": columns, "rows": data_rows, "totals": totals, "filters": f, "overview": overview}


def payment_proposal_report(connection, proposal):
    """Documento de conferência com resumo por funcionário e origem preservada."""
    obra = connection.execute("SELECT * FROM obras WHERE id=?", (proposal["obra_id"],)).fetchone()
    company = connection.execute("SELECT nome,cnpj,email,telefone,endereco,logo_mime,logo_dados FROM configuracoes_empresa WHERE id=1").fetchone()
    brand = dict(company) if company else {"nome": ""}
    logo_data = brand.pop("logo_dados", None)
    brand["logo_base64"] = base64.b64encode(logo_data).decode("ascii") if logo_data else ""
    people = {}
    detail = []
    for item in proposal["itens"]:
        employee = people.setdefault(item["funcionario_id"], {"funcionario": item["funcionario"], "linhas": 0, "valor_gerado": 0, "valor_pagar": 0})
        employee["linhas"] += 1
        employee["valor_gerado"] += float(item["valor_gerado"] or 0)
        employee["valor_pagar"] += float(item["valor_pagar"] or 0)
        local = " / ".join(f"{label} {item[key]}" for label, key in (("Bl.", "bloco"), ("AP", "ap"), ("Casa", "casa")) if item.get(key))
        detail.append({"funcionario": item["funcionario"], "servico": item["servico"], "local": local or "—",
                       "quantidade": item["quantidade"], "valor_gerado": item["valor_gerado"],
                       "valor_pagar": item["valor_pagar"], "observacao": item["observacao"] or ""})
    summary = sorted(people.values(), key=lambda row: row["funcionario"].casefold())
    for row in summary:
        row["valor_gerado"] = round(row["valor_gerado"], 2)
        row["valor_pagar"] = round(row["valor_pagar"], 2)
    return {"type": "proposta_pagamento", "title": f"PROPOSTA DE PAGAMENTO #{proposal['id']}",
            "intro": "Documento de conferência. Os valores propostos não comprovam pagamento nem quitação.",
            "obra": dict(obra), "company": brand,
            "filters": {"start": proposal["periodo_inicio"], "end": proposal["periodo_fim"], "pms": proposal["pms_numero"]},
            "summary_columns": [("FUNCIONÁRIO", "funcionario"), ("LINHAS", "linhas"),
                                ("PRODUZIDO A RECEBER (R$)", "valor_gerado"), ("PROPOSTO A PAGAR (R$)", "valor_pagar")],
            "summary_rows": summary,
            "columns": [("FUNCIONÁRIO", "funcionario"), ("SERVIÇO", "servico"), ("LOCAL", "local"),
                        ("QTD.", "quantidade"), ("PRODUZIDO A RECEBER (R$)", "valor_gerado"),
                        ("PROPOSTO A PAGAR (R$)", "valor_pagar"), ("OBSERVAÇÃO", "observacao")],
            "rows": detail,
            "totals": {"linhas": len(detail), "pagar": proposal["totais"]["valor_pagar"],
                       "receber": proposal["totais"]["valor_gerado"]}}


def _money(value):
    try:
        return f"R$ {float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value or "")


def pdf_bytes(report):
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        return _simple_pdf_bytes(report)
    buffer = io.BytesIO()
    portrait = report["type"] in {"reembolso"}
    page = A4 if portrait else landscape(A4)
    doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=9*mm, rightMargin=9*mm, topMargin=9*mm, bottomMargin=9*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PMS-title", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=14, leading=17, alignment=TA_CENTER, textColor=colors.HexColor("#17211f"))
    small = ParagraphStyle("PMS-small", parent=styles["BodyText"], fontName="Helvetica", fontSize=7, leading=9)
    period = f"{report['filters'].get('start') or 'início'} a {report['filters'].get('end') or 'fim'}"
    company = report.get("company", {})
    brand_image = None
    if company.get("logo_base64"):
        try:
            brand_image = Image(io.BytesIO(base64.b64decode(company["logo_base64"])), width=28*mm, height=17*mm, kind="proportional")
        except Exception:
            brand_image = None
    head = [
        [brand_image, Paragraph(report["title"], title_style)] if brand_image else [Paragraph(report["title"], title_style), ""],
        [Paragraph(f"<b>EMPRESA:</b> {html.escape(str(company.get('nome') or ''))} &nbsp; <b>CNPJ:</b> {html.escape(str(company.get('cnpj') or ''))}", small),
         Paragraph(f"<b>PMS:</b> {html.escape(str(report['filters'].get('pms') or 'não filtrado'))}", small)],
        [Paragraph(f"<b>CONTRATANTE:</b> {html.escape(str(report['obra'].get('cliente_contratante') or report['obra'].get('fornecedor') or ''))}", small),
         Paragraph(f"<b>PERÍODO:</b> {html.escape(period)}", small)],
    ]
    heading = Table(head, colWidths=[page[0]*0.58, page[0]*0.34])
    heading.setStyle(TableStyle([
        *([] if brand_image else [("SPAN",(0,0),(1,0))]),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("BOX",(0,0),(-1,-1),0.8,colors.HexColor("#17211f")),
        ("INNERGRID",(0,1),(-1,-1),0.35,colors.HexColor("#9aa8a3")),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dff1e9")),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    table_data = [[Paragraph(html.escape(label), small) for label, _ in report["columns"]]]
    money_keys = {"valor","extra","desconto","valor_padrao","valor_ajustado","total_pagar","valor_receber","valor_gerado","valor_pagar"}
    for row in report["rows"]:
        cells = []
        for _, key in report["columns"]:
            value = _money(row.get(key)) if key in money_keys else row.get(key)
            cells.append(Paragraph(html.escape(str(value if value is not None else "")), small))
        table_data.append(cells)
    if not report["rows"]:
        table_data.append([Paragraph("Nenhum registro para os filtros informados.", small)] + [""] * (len(report["columns"])-1))
    usable = page[0] - 18*mm
    widths = [usable / len(report["columns"])] * len(report["columns"])
    if report["type"] == "pms":
        proportions = [3.4, .7, .7, .7, .7, 1.2, 1.5]
        widths = [usable * x / sum(proportions) for x in proportions]
    elif report["type"] == "pagamento":
        proportions = [2, 2.6, 1.5, .7, 1.15, 1.1, 1.1, 1.5]
        widths = [usable * x / sum(proportions) for x in proportions]
    elif report["type"] == "proposta_pagamento":
        proportions = [1.8, 2.2, 1.5, .7, 1.4, 1.4, 2]
        widths = [usable * x / sum(proportions) for x in proportions]
    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#17211f")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#71807c")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f5f7f2")]),
        ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    total_text = (f"Linhas: {report['totals']['linhas']}   Produzido a receber: {_money(report['totals']['receber'])}   Proposto a pagar: {_money(report['totals']['pagar'])}"
                  if report["type"] == "proposta_pagamento" else
                  f"Registros: {report['totals']['linhas']}   Valor a pagar: {_money(report['totals']['pagar'])}   Valor a receber: {_money(report['totals']['receber'])}")
    content = [heading, Spacer(1, 4*mm)]
    if report.get("intro"):
        content.extend([Paragraph(html.escape(report["intro"]), small), Spacer(1, 3*mm)])
    if report.get("overview"):
        overview_table = Table([[Paragraph(html.escape(label), small), Paragraph(_money(value), small)] for label, value in report["overview"]],
                               colWidths=[usable*.63, usable*.37])
        overview_table.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.HexColor("#f5f7f2"),colors.white]),
            ("LINEBELOW",(0,-3),(-1,-3),0.8,colors.HexColor("#17211f")),
            ("LEFTPADDING",(0,0),(-1,-1),6), ("TOPPADDING",(0,0),(-1,-1),4),
        ]))
        content.extend([Paragraph("COMPOSIÇÃO DO FECHAMENTO", styles["Heading3"]), overview_table,
                        Spacer(1, 4*mm), Paragraph("ITENS DO FECHAMENTO", styles["Heading3"]), Spacer(1, 2*mm)])
    if report.get("summary_columns"):
        summary_data = [[Paragraph(html.escape(label), small) for label, _ in report["summary_columns"]]]
        for row in report["summary_rows"]:
            summary_data.append([Paragraph(html.escape(_money(row.get(key)) if key in money_keys else str(row.get(key, ""))), small)
                                 for _, key in report["summary_columns"]])
        if len(summary_data) == 1:
            summary_data.append([Paragraph("Nenhum funcionário nesta proposta.", small)] + [""] * (len(summary_data[0])-1))
        summary_table = Table(summary_data, colWidths=[usable*x for x in (.27, .09, .32, .32)], repeatRows=1)
        summary_table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dff1e9")),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f5f7f2")]),
            ("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#9aa8a3")),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
        ]))
        content.extend([Paragraph("RESUMO POR FUNCIONÁRIO", styles["Heading3"]), summary_table, Spacer(1, 4*mm),
                        Paragraph("SERVIÇOS E LOCAIS", styles["Heading3"]), Spacer(1, 2*mm)])
    content.extend([table, Spacer(1, 4*mm), Paragraph(total_text, small)])
    doc.build(content)
    return buffer.getvalue()


def _simple_pdf_bytes(report):
    """PDF em blocos legíveis, paginado, sem bibliotecas externas."""
    def clean(value):
        return " ".join(str(value if value is not None else "").replace("\r", " ").replace("\n", " ").replace("\u2014", "-").split())

    def escaped(value):
        return clean(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    money_keys = {"valor", "extra", "desconto", "valor_padrao", "valor_ajustado", "total_pagar", "valor_receber", "valor_gerado", "valor_pagar"}
    lines = []

    def add(value, kind="body"):
        for part in textwrap.wrap(clean(value), width=115, break_long_words=False, break_on_hyphens=False) or [""]:
            lines.append((part, kind))

    if report.get("intro"):
        add(report["intro"], "note")
        add("")
    if report.get("overview"):
        add("COMPOSIÇÃO DO FECHAMENTO", "section")
        for label, value in report["overview"]:
            add(f"{label}: {_money(value)}")
        add("")
        add("ITENS DO FECHAMENTO", "section")
    if report.get("summary_columns"):
        add("RESUMO POR FUNCIONÁRIO", "section")
        for row in report["summary_rows"]:
            parts = [clean(row.get("funcionario"))]
            parts.extend(f"{label}: {_money(row.get(key)) if key in money_keys else clean(row.get(key))}"
                         for label, key in report["summary_columns"] if key != "funcionario")
            add("  |  ".join(parts))
        if not report["summary_rows"]:
            add("Nenhum funcionário nesta proposta.")
        add("")
        add("SERVIÇOS E LOCAIS", "section")
    if not report["rows"]:
        add("Nenhum registro para os filtros informados.")
    for row in report["rows"]:
        parts = [f"{label}: {_money(row.get(key)) if key in money_keys else clean(row.get(key))}"
                 for label, key in report["columns"] if row.get(key) not in (None, "")]
        for index in range(0, len(parts), 3):
            add("  |  ".join(parts[index:index+3]))
        add("", "gap")
    add(f"TOTAL - {report['totals']['linhas']} linhas  |  Produzido a receber: {_money(report['totals']['receber'])}  |  A pagar: {_money(report['totals']['pagar'])}", "total")

    period = f"{report['filters'].get('start') or 'início'} a {report['filters'].get('end') or 'fim'}"
    pages = [lines[index:index+32] for index in range(0, len(lines), 32)] or [[]]
    objects = [None, b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    page_ids = []
    for page_number, page_lines in enumerate(pages, 1):
        page_id = len(objects)
        content_id = page_id + 1
        page_ids.append(page_id)
        commands = ["0.09 0.14 0.12 rg", "32 512 778 52 re f", "1 1 1 rg",
                    f"BT /F2 16 Tf 44 543 Td ({escaped(report['title'])}) Tj ET",
                    "0.10 0.15 0.13 rg",
                    f"BT /F1 9 Tf 44 496 Td ({escaped(report['obra'].get('nome') or '')}  |  {escaped(period)}  |  PMS {escaped(report['filters'].get('pms') or '-')} ) Tj ET",
                    f"BT /F1 8 Tf 44 481 Td ({escaped(report.get('company',{}).get('nome') or '')}) Tj ET",
                    "0.76 0.83 0.78 RG", "44 471 m 798 471 l S"]
        y = 451
        for value, kind in page_lines:
            if kind == "gap":
                y -= 4
                continue
            font = "F2" if kind in ("section", "total") else "F1"
            size = 10 if kind in ("section", "total") else 8.5
            if kind == "section":
                commands.append(f"0.88 0.94 0.90 rg 44 {y-5} 754 16 re f 0.10 0.15 0.13 rg")
            commands.append(f"BT /{font} {size} Tf 48 {y} Td ({escaped(value)}) Tj ET")
            y -= 11 if kind == "body" else 14
        commands.extend(["0.76 0.83 0.78 RG", "44 34 m 798 34 l S", "0.3 0.35 0.32 rg",
                         f"BT /F1 8 Tf 44 22 Td (Documento de conferência  |  Página {page_number} de {len(pages)}) Tj ET"])
        stream = "\n".join(commands).encode("cp1252", "replace")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[2] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, len(objects)):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def xlsx_bytes(report):
    """Gera uma planilha XLSX válida usando apenas a biblioteca padrão."""
    def column_name(index):
        name = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    def string_cell(reference, value, style=0):
        safe = html.escape(str(value if value is not None else ""), quote=False)
        return f'<c r="{reference}" t="inlineStr" s="{style}"><is><t>{safe}</t></is></c>'

    def value_cell(reference, value, style=0):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
        return string_cell(reference, value, style)

    columns = report["columns"]
    last_column = column_name(max(1, len(columns)))
    period = f"{report['filters'].get('start') or 'início'} a {report['filters'].get('end') or 'fim'}"
    rows = [
        f'<row r="1" ht="26" customHeight="1">{string_cell("A1", report["title"], 1)}</row>',
        f'<row r="2">{string_cell("A2", "EMPRESA", 2)}{string_cell("B2", report.get("company",{}).get("nome") or "")}{string_cell("C2", "CNPJ", 2)}{string_cell("D2", report.get("company",{}).get("cnpj") or "")}</row>',
        f'<row r="3">{string_cell("A3", "OBRA", 2)}{string_cell("B3", report["obra"].get("nome") or "")}{string_cell("C3", "PERÍODO", 2)}{string_cell("D3", period)}</row>',
        f'<row r="4">{string_cell("A4", "PMS", 2)}{string_cell("B4", report["filters"].get("pms") or "não filtrado")}</row>',
    ]
    currency_keys = {"valor", "extra", "desconto", "valor_padrao", "valor_ajustado", "total_pagar", "valor_receber", "valor_gerado", "valor_pagar"}
    detail_header = 6
    if report.get("overview"):
        rows.append(f'<row r="5">{string_cell("A5", "COMPOSIÇÃO DO FECHAMENTO", 2)}</row>')
        for row_number, (label, value) in enumerate(report["overview"], 6):
            rows.append(f'<row r="{row_number}">{string_cell(f"A{row_number}", label, 2)}{value_cell(f"D{row_number}", value, 4)}</row>')
        detail_header = 8 + len(report["overview"])
        rows.append(f'<row r="{detail_header-1}">{string_cell(f"A{detail_header-1}", "ITENS DO FECHAMENTO", 2)}</row>')
    if report.get("summary_columns"):
        rows.append(f'<row r="5">{string_cell("A5", report.get("intro", ""), 2)}</row>')
        rows.append(f'<row r="6">{string_cell("A6", "RESUMO POR FUNCIONÁRIO", 2)}</row>')
        summary_headers = "".join(string_cell(f"{column_name(index)}7", label, 3) for index, (label, _) in enumerate(report["summary_columns"], 1))
        rows.append(f'<row r="7" ht="42" customHeight="1">{summary_headers}</row>')
        for row_number, item in enumerate(report["summary_rows"], 8):
            cells = "".join(value_cell(f"{column_name(index)}{row_number}", item.get(key), 4 if key in currency_keys and isinstance(item.get(key), (int, float)) else 0)
                            for index, (_, key) in enumerate(report["summary_columns"], 1))
            rows.append(f'<row r="{row_number}">{cells}</row>')
        detail_header = 10 + len(report["summary_rows"])
        rows.append(f'<row r="{detail_header-1}">{string_cell(f"A{detail_header-1}", "SERVIÇOS E LOCAIS", 2)}</row>')
    header_cells = "".join(string_cell(f"{column_name(index)}{detail_header}", label, 3) for index, (label, _) in enumerate(columns, 1))
    rows.append(f'<row r="{detail_header}" ht="42" customHeight="1">{header_cells}</row>')
    for row_number, item in enumerate(report["rows"], detail_header+1):
        cells = []
        for index, (_, key) in enumerate(columns, 1):
            value = item.get(key)
            style = 4 if key in currency_keys and isinstance(value, (int, float)) else 0
            cells.append(value_cell(f"{column_name(index)}{row_number}", value, style))
        rows.append(f'<row r="{row_number}" ht="30" customHeight="1">{"".join(cells)}</row>')
    if not report["rows"]:
        rows.append(f'<row r="{detail_header+1}">{string_cell(f"A{detail_header+1}", "Nenhum registro para os filtros informados.")}</row>')
    if report.get("summary_columns"):
        total_row = detail_header + len(report["rows"]) + 2
        rows.append(f'<row r="{total_row}">{string_cell(f"A{total_row}", "TOTAL DA PROPOSTA", 2)}'
                    f'{value_cell(f"E{total_row}", report["totals"]["receber"], 4)}'
                    f'{value_cell(f"F{total_row}", report["totals"]["pagar"], 4)}</row>')
    widths = "".join(f'<col min="{index}" max="{index}" width="{29 if key in {"servico", "servico_pms", "descricao", "observacao"} else 24 if key in {"funcionario", "local"} else 21 if key in {"valor_gerado", "valor_pagar", "total_pagar", "valor_receber"} else 13}" customWidth="1"/>' for index, (_, key) in enumerate(columns, 1))
    frozen = 4 if report.get("summary_columns") or report.get("overview") else detail_header
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><sheetViews><sheetView workbookViewId="0"><pane ySplit="{frozen}" topLeftCell="A{frozen+1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>{widths}</cols><sheetData>{"".join(rows)}</sheetData><autoFilter ref="A{detail_header}:{last_column}{max(detail_header, detail_header + len(report['rows']))}"/><mergeCells count="1"><mergeCell ref="A1:{last_column}1"/></mergeCells><pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Relatório PMS" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode='&quot;R$&quot; #,##0.00'/></numFmts><fonts count="2"><font><sz val="10"/><name val="Arial"/></font><font><b/><sz val="10"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDFF1E9"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="5"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf><xf fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf fontId="0" fillId="0" borderId="0" numFmtId="164" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def print_html(report):
    heads = "".join(f"<th>{html.escape(label)}</th>" for label, _ in report["columns"])
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(key) if row.get(key) is not None else ''))}</td>" for _, key in report["columns"]) + "</tr>" for row in report["rows"])
    company = report.get("company", {})
    brand_name = html.escape(str(company.get("nome") or ""))
    brand_cnpj = html.escape(str(company.get("cnpj") or ""))
    brand_contact = html.escape(" · ".join(value for value in (company.get("email"), company.get("telefone")) if value))
    logo = (f'<img class="brand-logo" alt="Marca da empresa" src="data:{html.escape(str(company.get("logo_mime") or "image/png"))};base64,{company.get("logo_base64")}">'
            if company.get("logo_base64") else "")
    summary_html = ""
    if report.get("overview"):
        overview_rows = "".join(f"<tr><th>{html.escape(label)}</th><td>{html.escape(_money(value))}</td></tr>" for label, value in report["overview"])
        summary_html += f"<h2>Composição do fechamento</h2><table><tbody>{overview_rows}</tbody></table><h2>Itens do fechamento</h2>"
    if report.get("summary_columns"):
        summary_heads = "".join(f"<th>{html.escape(label)}</th>" for label, _ in report["summary_columns"])
        summary_rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(key) if row.get(key) is not None else ''))}</td>" for _, key in report["summary_columns"]) + "</tr>" for row in report["summary_rows"])
        summary_html = f"<h2>Resumo por funcionário</h2><table><thead><tr>{summary_heads}</tr></thead><tbody>{summary_rows}</tbody></table><h2>Serviços e locais</h2>"
    intro = f"<p>{html.escape(report['intro'])}</p>" if report.get("intro") else ""
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>{html.escape(report['title'])}</title>
    <style>@page{{size:A4 landscape;margin:10mm}}body{{font:10px Arial;color:#17211f}}h1{{font-size:18px;text-align:center}}
    .meta{{display:grid;grid-template-columns:1fr 1fr;border:1px solid #17211f;margin-bottom:12px}}.meta div{{padding:6px;border:1px solid #aaa}}.brand{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}.brand-logo{{max-width:90px;max-height:54px;object-fit:contain}}
    table{{border-collapse:collapse;width:100%}}th{{background:#17211f;color:#fff}}th,td{{border:1px solid #71807c;padding:4px;text-align:left;vertical-align:top}}
    tbody tr:nth-child(even){{background:#f5f7f2}}@media print{{button{{display:none}}}}</style></head><body>
    <button onclick="print()">Imprimir</button><div class="brand">{logo}<div><strong>{brand_name}</strong><div>{brand_cnpj}</div><div>{brand_contact}</div></div></div><h1>{html.escape(report['title'])}</h1>
    <div class="meta"><div><b>OBRA:</b> {html.escape(str(report['obra']['nome']))}</div><div><b>PMS:</b> {html.escape(str(report['filters'].get('pms') or 'não filtrado'))}</div><div><b>PERÍODO:</b> {html.escape(str(report['filters'].get('start') or 'início'))} a {html.escape(str(report['filters'].get('end') or 'fim'))}</div><div></div></div>
    {intro}{summary_html}<table><thead><tr>{heads}</tr></thead><tbody>{rows}</tbody></table>
    <p><b>Produzido a receber:</b> {_money(report['totals']['receber'])} &nbsp; <b>Proposto a pagar:</b> {_money(report['totals']['pagar'])}</p></body></html>"""
