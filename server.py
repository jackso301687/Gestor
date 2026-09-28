"""Servidor web local do sistema O gestor de Campo. Execute: python3 server.py"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import hashlib
import hmac
import os
import re
import sqlite3
import sys
from datetime import date
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from database import (
    ROOT, PROFILES, adjust_payment_item, audit, authenticate, change_password, deactivate_entry,
    connect, correct_service_price, create_appropriation, create_entry,
    create_employee_service_price, create_service_price, dashboard, generate_closing,
    has_permission, has_work_access, hash_password, init_db, payment_component, public_user,
    list_employee_service_prices, recalculate_closing, remove_payment_component, save_quality, session_user,
    update_entry, validate_password,
)
from reports import pdf_bytes, print_html, report_data, xlsx_bytes, payment_proposal_report
from apontamento_store import merge_payload as merge_apontamento_payload
from apontamento_store import snapshot as apontamento_snapshot
from catalog_excel import parse_catalog_xlsx
import settings_store
import proposta_store
import proposta_pagamento_store
from proposta_export import pdf_bytes as proposta_pdf_bytes, xlsx_bytes as proposta_xlsx_bytes

HOST = os.environ.get("PMS_HOST", "127.0.0.1")
PORT = int(os.environ.get("PMS_PORT", "8080"))
SECURE_COOKIE = os.environ.get("PMS_SECURE_COOKIE", "0") == "1"
def validate_period(start, end):
    """Valida filtros globais sem alterar as datas dos registros."""
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
    except (TypeError, ValueError) as exc:
        raise ValueError("Período inválido. Informe datas no formato AAAA-MM-DD.") from exc
    if start_date and end_date and start_date > end_date:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    return start, end


class PMSHandler(BaseHTTPRequestHandler):
    server_version = "GestorDeCampo/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, content_type, filename=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def raw_session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie.get("pms_session").value if cookie.get("pms_session") else None

    def current_user(self, db):
        return session_user(db, self.raw_session_token())

    def require_user(self, db, role=None, permission=None, csrf=False):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sessão expirada ou acesso não autenticado"}, 401)
            return None
        if user["deve_trocar_senha"] and self.path not in ("/api/auth/me", "/api/auth/change-password", "/api/auth/logout"):
            self.send_json({"error": "Troque a senha temporária antes de continuar", "code": "PASSWORD_CHANGE_REQUIRED"}, 403)
            return None
        if role and user["role"] != role:
            self.send_json({"error": "Esta operação exige acesso de administrador"}, 403)
            return None
        if permission and not has_permission(user, permission):
            self.send_json({"error": "Seu perfil não possui permissão para esta operação"}, 403)
            return None
        if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), user["csrf_token"]):
            self.send_json({"error": "Token de segurança inválido. Atualize a página."}, 403)
            return None
        return user

    def require_work(self, db, user, obra_id):
        if has_work_access(db, user, obra_id):
            return True
        self.send_json({"error": "Você não possui acesso a esta obra"}, 403)
        return False

    def read_json(self):
        size = int(self.headers.get("Content-Length", 0))
        if size < 0 or size > 32 * 1024 * 1024:
            raise ValueError("Requisição excede o limite de 32 MB")
        payload = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Envie um objeto JSON")
        return payload

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.api_get(parsed.path, parse_qs(parsed.query))
        if parsed.path == "/campo":
            self.send_response(302)
            self.send_header("Location", "/campo/" + ("?" + parsed.query if parsed.query else ""))
            self.end_headers()
            return
        path = parsed.path.lstrip("/")
        if not path or path.endswith("/"):
            path += "index.html"
        # Somente recursos públicos explícitos; nunca servir bancos, backups ou código Python.
        public_files = {
            "index.html", "login.html", "app-system.js", "auth.js", "local-launch.js",
            "tokens.css", "styles.css", "system.css", "auth.css",
            "campo/index.html", "campo/app.js", "campo/styles.css", "campo/sw.js",
            "campo/manifest.webmanifest", "campo/icons/icon.svg",
        }
        if path not in public_files:
            return self.send_error(HTTPStatus.NOT_FOUND)
        target = (ROOT / path).resolve()
        if ROOT not in target.parents and target != ROOT:
            return self.send_error(HTTPStatus.FORBIDDEN)
        if not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path, query):
        try:
            obra_id = int(query.get("obra_id", [1])[0])
        except (TypeError, ValueError):
            return self.send_json({"error": "Obra inválida"}, 400)
        try:
            start, end = validate_period(query.get("start", [None])[0], query.get("end", [None])[0])
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        with connect() as db:
            user = self.require_user(db)
            if not user:
                return
            if path == "/api/auth/me":
                return self.send_json({"user": public_user(user), "csrf_token": user["csrf_token"], "expires_at": user["expira_em"]})
            if path == "/api/settings":
                if not self.require_work(db, user, obra_id):
                    return
                settings_store.sync_field_settings(db, obra_id, user["id"], persist=False)
                return self.send_json(settings_store.get_settings(db, obra_id, user, user["sessao_id"]))
            if path == "/api/settings/field-links":
                if not has_permission(user, "lancamentos"):
                    return self.send_json({"error": "Seu perfil não possui acesso ao apontamento de campo"}, 403)
                if not self.require_work(db, user, obra_id):
                    return
                return self.send_json(settings_store.field_link_suggestions(db, obra_id))
            if path == "/api/settings/field-appointments":
                if not has_permission(user, "lancamentos"):
                    return self.send_json({"error": "Seu perfil não possui acesso ao apontamento de campo"}, 403)
                if not self.require_work(db, user, obra_id):
                    return
                return self.send_json(settings_store.list_field_appointments(db, obra_id))
            if path == "/api/settings/audit":
                if user["perfil"] != "admin":
                    return self.send_json({"error": "Auditoria exige perfil administrador"}, 403)
                page = max(1, int(query.get("page", [1])[0]))
                limit = min(100, max(10, int(query.get("limit", [25])[0])))
                filters, params = [], []
                for key, column in (("entidade", "a.entidade"), ("acao", "a.acao"), ("usuario", "u.nome")):
                    value = query.get(key, [""])[0].strip()
                    if value:
                        filters.append(f"{column} LIKE ?")
                        params.append(f"%{value}%")
                if start:
                    filters.append("date(a.criado_em)>=?"); params.append(start)
                if end:
                    filters.append("date(a.criado_em)<=?"); params.append(end)
                search = query.get("q", [""])[0].strip()
                if search:
                    filters.append("(a.entidade LIKE ? OR a.acao LIKE ? OR u.nome LIKE ? OR a.detalhes LIKE ? OR a.antes LIKE ? OR a.depois LIKE ?)")
                    params.extend([f"%{search}%"] * 6)
                where = " WHERE " + " AND ".join(filters) if filters else ""
                total = db.execute(
                    "SELECT COUNT(*) FROM auditoria a LEFT JOIN usuarios u ON u.id=a.usuario_id" + where,
                    params,
                ).fetchone()[0]
                rows = db.execute(
                    "SELECT a.*,u.nome usuario FROM auditoria a LEFT JOIN usuarios u ON u.id=a.usuario_id" +
                    where + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
                    [*params, limit, (page - 1) * limit],
                ).fetchall()
                return self.send_json({"items": [dict(row) for row in rows], "total": total,
                                       "page": page, "pages": (total + limit - 1) // limit})
            if path == "/api/settings/backup":
                if user["perfil"] != "admin":
                    return self.send_json({"error": "Backup exige perfil administrador"}, 403)
                stamp = date.today().strftime("%Y%m%d")
                return self.send_bytes(settings_store.backup_database_bytes(db), "application/vnd.sqlite3", f"pms-backup-{stamp}.sqlite3")
            if path in ("/api/apontamento/health", "/api/apontamento/snapshot"):
                if not has_permission(user, "lancamentos"):
                    return self.send_json({"error": "Seu perfil não possui acesso ao apontamento de campo"}, 403)
                if not self.require_work(db, user, obra_id):
                    return
                if path == "/api/apontamento/health":
                    total = db.execute(
                        "SELECT COUNT(*) FROM apontamento_records WHERE obra_id=?", (obra_id,)
                    ).fetchone()[0]
                    return self.send_json({"ok": True, "database": "pms", "records": total})
                settings_store.ensure_field_catalog(db, obra_id, user["id"])
                result = apontamento_snapshot(db, obra_id)
                result["settings"] = [settings_store.sync_field_settings(db, obra_id, user["id"], persist=False)]
                return self.send_json(result)
            if path == "/api/users":
                if user["role"] != "admin":
                    return self.send_json({"error": "Esta área exige acesso de administrador"}, 403)
                rows = db.execute("SELECT id,nome,username,email,role,perfil,ativo,deve_trocar_senha,ultimo_login,criado_em,atualizado_em,versao FROM usuarios ORDER BY nome").fetchall()
                result = []
                for row in rows:
                    item = dict(row)
                    item["obra_ids"] = [x["obra_id"] for x in db.execute("SELECT obra_id FROM usuario_obras WHERE usuario_id=? ORDER BY obra_id", (row["id"],))]
                    result.append(item)
                return self.send_json(result)
            if path == "/api/works":
                if (user["perfil"] or ("admin" if user["role"] == "admin" else "operador")) == "admin":
                    rows = db.execute("SELECT * FROM obras ORDER BY nome").fetchall()
                else:
                    rows = db.execute(
                        """SELECT o.* FROM obras o JOIN usuario_obras uo ON uo.obra_id=o.id
                           WHERE uo.usuario_id=? ORDER BY o.nome""",
                        (user["id"],),
                    ).fetchall()
                return self.send_json([dict(x) for x in rows])
            work_scoped = {
                "/api/bootstrap", "/api/dashboard", "/api/entries", "/api/employees",
                "/api/services", "/api/payments", "/api/pms", "/api/appropriations",
                "/api/quality", "/api/summary", "/api/forecast", "/api/reimbursements",
                "/api/closings", "/api/report.csv",
            }
            if (path in work_scoped or path.startswith("/api/reports/")) and not self.require_work(db, user, obra_id):
                return
            if path == "/api/bootstrap":
                obra = db.execute("SELECT * FROM obras WHERE id=?", (obra_id,)).fetchone()
                if not obra:
                    return self.send_json({"error": "Obra não encontrada"}, 404)
                dates = db.execute("SELECT MIN(data) inicio, MAX(data) fim FROM lancamentos WHERE obra_id=? AND ativo=1", (obra_id,)).fetchone()
                return self.send_json({
                    "obra": dict(obra), "periodo": dict(dates), "filtro": {"inicio": start, "fim": end},
                    "dashboard": dashboard(db, obra_id, start, end),
                    "funcionarios": [dict(x) for x in db.execute("SELECT * FROM funcionarios WHERE obra_id=? ORDER BY nome", (obra_id,))],
                    "servicos": [dict(x) for x in db.execute("SELECT * FROM servicos WHERE obra_id=? ORDER BY nome_interno", (obra_id,))],
                })
            if path == "/api/dashboard":
                return self.send_json(dashboard(db, obra_id, start, end))
            if path == "/api/entries":
                page = max(1, int(query.get("page", [1])[0])); limit = min(100, max(10, int(query.get("limit", [25])[0])))
                search = query.get("search", [""])[0].strip()
                where, params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: where.append("l.data>=?"); params.append(start)
                if end: where.append("l.data<=?"); params.append(end)
                if search:
                    where.append("(f.nome LIKE ? OR s.nome_interno LIKE ? OR COALESCE(l.casa,'') LIKE ?)")
                    params.extend([f"%{search}%"] * 3)
                clause = " AND ".join(where)
                total = db.execute(f"SELECT COUNT(*) FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id JOIN servicos s ON s.id=l.servico_id WHERE {clause}", params).fetchone()[0]
                rows = db.execute(f"SELECT l.*, f.nome funcionario, f.profissao, s.nome_interno servico, s.unidade FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id JOIN servicos s ON s.id=l.servico_id WHERE {clause} ORDER BY l.data DESC,l.id DESC LIMIT ? OFFSET ?", [*params, limit, (page-1)*limit]).fetchall()
                return self.send_json({"items": [dict(x) for x in rows], "total": total, "page": page, "pages": (total + limit - 1)//limit})
            if path == "/api/employees":
                join = ["l.funcionario_id=f.id", "l.ativo=1"]
                params = []
                if start: join.append("l.data>=?"); params.append(start)
                if end: join.append("l.data<=?"); params.append(end)
                params.append(obra_id)
                rows = db.execute(
                    f"""SELECT f.*,COUNT(l.id) lancamentos,
                               ROUND(COALESCE(SUM(l.valor_pagar),0),2) total_pago
                        FROM funcionarios f LEFT JOIN lancamentos l ON {' AND '.join(join)}
                        WHERE f.obra_id=? GROUP BY f.id ORDER BY f.nome""", params,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/services":
                effective_date = end or date.today().isoformat()
                join_period = ""
                named = {"obra_id": obra_id, "effective_date": effective_date}
                if start: join_period += " AND l.data>=:start"; named["start"] = start
                if end: join_period += " AND l.data<=:end"; named["end"] = end
                rows = db.execute(
                    """SELECT s.*,COUNT(l.id) lancamentos,ROUND(COALESCE(SUM(l.valor_receber),0),2) faturamento,
                              COALESCE((SELECT p.preco_pagamento FROM servico_precos p
                                        WHERE p.servico_id=s.id AND p.ativo=1 AND p.inicio_vigencia<=:effective_date
                                          AND (p.fim_vigencia IS NULL OR p.fim_vigencia>=:effective_date)
                                        ORDER BY p.inicio_vigencia DESC LIMIT 1),s.preco_pagamento) preco_pagamento_atual,
                              COALESCE((SELECT p.valor_receber_unitario FROM servico_precos p
                                        WHERE p.servico_id=s.id AND p.ativo=1 AND p.inicio_vigencia<=:effective_date
                                          AND (p.fim_vigencia IS NULL OR p.fim_vigencia>=:effective_date)
                                        ORDER BY p.inicio_vigencia DESC LIMIT 1),s.valor_receber_unitario) valor_receber_atual
                       FROM servicos s LEFT JOIN lancamentos l ON l.servico_id=s.id AND l.ativo=1""" + join_period + """
                       WHERE s.obra_id=:obra_id GROUP BY s.id ORDER BY s.nome_interno""",
                    named,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/employee-service-prices":
                if not has_permission(user, "servico.preco.alterar"):
                    return self.send_json({"error": "Seu perfil não pode consultar preços específicos"}, 403)
                work_id = int(query.get("obra_id", [obra_id])[0])
                service_id = int(query.get("servico_id", [0])[0])
                if not self.require_work(db, user, work_id): return
                return self.send_json(list_employee_service_prices(db, work_id, service_id))
            if path.startswith("/api/services/") and path.endswith("/prices"):
                parts = path.strip("/").split("/")
                service_id = int(parts[2])
                service = db.execute("SELECT * FROM servicos WHERE id=?", (service_id,)).fetchone()
                if not service:
                    return self.send_json({"error": "Serviço não encontrado"}, 404)
                if not self.require_work(db, user, service["obra_id"]): return
                rows = db.execute(
                    """SELECT p.*,u.nome criado_por_nome,ua.nome atualizado_por_nome
                       FROM servico_precos p
                       LEFT JOIN usuarios u ON u.id=p.criado_por
                       LEFT JOIN usuarios ua ON ua.id=p.atualizado_por
                       WHERE p.servico_id=? AND p.ativo=1
                       ORDER BY p.inicio_vigencia DESC,p.id DESC""",
                    (service_id,),
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/history":
                if user["perfil"] != "admin":
                    return self.send_json({"error": "Histórico administrativo exige administrador"}, 403)
                entity = query.get("entity", [""])[0]
                entity_id = int(query.get("id", [0])[0])
                allowed = {"obras", "funcionarios", "servicos", "funcionario_servico_precos", "lancamento", "usuario", "reembolso", "apropriacao", "medicao_pms"}
                if entity not in allowed:
                    return self.send_json({"error": "Entidade de histórico inválida"}, 400)
                rows = db.execute(
                    """SELECT a.*,u.nome usuario FROM auditoria a LEFT JOIN usuarios u ON u.id=a.usuario_id
                       WHERE a.entidade=? AND a.entidade_id=? ORDER BY a.id DESC""",
                    (entity, entity_id),
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/payments":
                where, params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: where.append("l.data>=?"); params.append(start)
                if end: where.append("l.data<=?"); params.append(end)
                rows = db.execute(f"SELECT f.id,f.nome,f.profissao,COUNT(l.id) lancamentos,ROUND(SUM(l.valor_pagar),2) valor_liquido,ROUND(SUM(l.desconto),2) desconto,ROUND(SUM(l.extra),2) extra FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id WHERE {' AND '.join(where)} GROUP BY f.id ORDER BY valor_liquido DESC", params).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/pms":
                measurement_where, measurement_params = ["obra_id=?"], [obra_id]
                if start: measurement_where.append("periodo_fim>=?"); measurement_params.append(start)
                if end: measurement_where.append("periodo_inicio<=?"); measurement_params.append(end)
                measurement = db.execute(
                    f"SELECT * FROM medicoes_pms WHERE {' AND '.join(measurement_where)} ORDER BY numero DESC LIMIT 1",
                    measurement_params,
                ).fetchone()
                item_where, item_params = ["l.obra_id=?", "l.ativo=1", "l.valor_receber>0"], [obra_id]
                if start: item_where.append("l.data>=?"); item_params.append(start)
                if end: item_where.append("l.data<=?"); item_params.append(end)
                items = db.execute(
                    f"""SELECT s.id servico_id,s.nome_interno,s.descricao_pms,
                               ROUND(SUM(COALESCE(l.quantidade_m2,0)),4) quantidade,
                               ROUND(SUM(l.valor_receber),2) total_a_receber
                        FROM lancamentos l JOIN servicos s ON s.id=l.servico_id
                        WHERE {' AND '.join(item_where)} GROUP BY s.id,s.nome_interno,s.descricao_pms
                        ORDER BY total_a_receber DESC""", item_params,
                ).fetchall()
                discounts = db.execute("SELECT * FROM descontos_servico WHERE obra_id=?", (obra_id,)).fetchall()
                return self.send_json({"medicao": dict(measurement) if measurement else None, "itens": [dict(x) for x in items], "descontos": [dict(x) for x in discounts]})
            if path == "/api/proposals":
                if not has_permission(user, "pms"):
                    return self.send_json({"error":"Seu perfil não pode consultar propostas de medição"},403)
                return self.send_json(proposta_store.list_proposals(db, obra_id))
            proposal_match = re.fullmatch(r"/api/proposals/(\d+)(?:\.(xlsx|pdf))?", path)
            if proposal_match:
                if not has_permission(user, "pms"):
                    return self.send_json({"error":"Seu perfil não pode consultar propostas de medição"},403)
                proposal_id, output_format = int(proposal_match.group(1)), proposal_match.group(2)
                proposal = proposta_store.proposal_data(db, proposal_id)
                if not self.require_work(db, user, proposal["obra_id"]): return
                if output_format == "xlsx":
                    return self.send_bytes(proposta_xlsx_bytes(proposal), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"proposta-medicao-{proposal_id}.xlsx")
                if output_format == "pdf":
                    return self.send_bytes(proposta_pdf_bytes(proposal), "application/pdf", f"proposta-medicao-{proposal_id}.pdf")
                return self.send_json(proposal)
            if path == "/api/appropriations":
                where, params = ["a.obra_id=?"], [obra_id]
                if start: where.append("a.data>=?"); params.append(start)
                if end: where.append("a.data<=?"); params.append(end)
                employee = query.get("funcionario_id", [None])[0]
                if employee: where.append("a.funcionario_id=?"); params.append(employee)
                rows = db.execute(
                    f"""SELECT a.*,f.nome funcionario,f.profissao,s.nome_interno servico
                        FROM apropriacoes a JOIN funcionarios f ON f.id=a.funcionario_id
                        JOIN servicos s ON s.id=a.servico_id WHERE {' AND '.join(where)}
                        ORDER BY a.data DESC,f.nome,a.id""", params,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/quality":
                situation = query.get("situacao", ["todos"])[0]
                page = max(1, int(query.get("page", [1])[0]))
                limit = min(100, max(10, int(query.get("limit", [25])[0])))
                where, params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: where.append("l.data>=?"); params.append(start)
                if end: where.append("l.data<=?"); params.append(end)
                if situation != "todos":
                    where.append("COALESCE(q.situacao,'nao_avaliado')=?"); params.append(situation)
                rows = db.execute(
                    f"""SELECT l.id lancamento_id,l.data,f.nome funcionario,s.nome_interno servico,
                               s.descricao_pms,l.casa,l.bloco,l.ap,
                               COALESCE(q.situacao,'nao_avaliado') situacao,q.observacao,q.avaliado_em
                        FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id
                        JOIN servicos s ON s.id=l.servico_id
                        LEFT JOIN avaliacoes_qualidade q ON q.lancamento_id=l.id
                        WHERE {' AND '.join(where)} ORDER BY l.data DESC,l.id DESC LIMIT ? OFFSET ?""", [*params, limit, (page-1)*limit],
                ).fetchall()
                total_where, total_params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: total_where.append("l.data>=?"); total_params.append(start)
                if end: total_where.append("l.data<=?"); total_params.append(end)
                counts = db.execute(
                    f"""SELECT COALESCE(q.situacao,'nao_avaliado') situacao,COUNT(*) total
                       FROM lancamentos l LEFT JOIN avaliacoes_qualidade q ON q.lancamento_id=l.id
                       WHERE {' AND '.join(total_where)} GROUP BY COALESCE(q.situacao,'nao_avaliado')""", total_params,
                ).fetchall()
                totals = {x["situacao"]:x["total"] for x in counts}
                selected_total = sum(totals.values()) if situation == "todos" else totals.get(situation, 0)
                return self.send_json({"items":[dict(x) for x in rows],"totals":totals,"total":selected_total,"page":page,"pages":(selected_total+limit-1)//limit,"limit":limit})
            if path.startswith("/api/quality/") and path.endswith("/history"):
                launch_id = int(path.strip("/").split("/")[2])
                launch = db.execute("SELECT obra_id FROM lancamentos WHERE id=? AND ativo=1", (launch_id,)).fetchone()
                if not launch: return self.send_json({"error": "Lançamento não encontrado"}, 404)
                if not self.require_work(db, user, launch["obra_id"]): return
                rows = db.execute(
                    """SELECT h.*,u.nome tecnico FROM qualidade_historico h
                       LEFT JOIN usuarios u ON u.id=h.tecnico_id
                       WHERE h.lancamento_id=? ORDER BY h.id DESC""",
                    (launch_id,),
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/summary":
                report = report_data(db, "resumo", query)
                summary_where, summary_params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: summary_where.append("l.data>=?"); summary_params.append(start)
                if end: summary_where.append("l.data<=?"); summary_params.append(end)
                by_service = db.execute(
                    f"""SELECT f.nome funcionario,s.nome_interno servico,l.casa,l.bloco,l.ap,l.obs,
                              ROUND(SUM(COALESCE(l.quantidade_m2,0)),4) quantidade,
                              ROUND(SUM(l.extra),2) extra,ROUND(SUM(l.desconto),2) desconto,
                              ROUND(SUM(l.valor_pagar),2) valor
                       FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id
                       JOIN servicos s ON s.id=l.servico_id WHERE {' AND '.join(summary_where)}
                       GROUP BY f.id,s.id,l.casa,l.bloco,l.ap,l.obs ORDER BY f.nome,s.nome_interno LIMIT 1000""",
                    summary_params,
                ).fetchall()
                return self.send_json({"totals":report["totals"],"items":[dict(x) for x in by_service]})
            if path == "/api/forecast":
                rows = db.execute("SELECT * FROM previsao_pms WHERE obra_id=? ORDER BY id", (obra_id,)).fetchall()
                total = sum(float(x["valor_total"] or 0) for x in rows)
                discount = db.execute("SELECT COALESCE(SUM(valor_desconto),0) FROM descontos_servico WHERE obra_id=?", (obra_id,)).fetchone()[0]
                return self.send_json({"items":[dict(x) for x in rows],"total_sem_desconto":round(total,2),"desconto":round(discount,2),"total_com_desconto":round(total-discount,2)})
            if path == "/api/reimbursements":
                reimbursement_where, reimbursement_params = ["r.obra_id=?"], [obra_id]
                if start: reimbursement_where.append("r.data>=?"); reimbursement_params.append(start)
                if end: reimbursement_where.append("r.data<=?"); reimbursement_params.append(end)
                rows = db.execute(
                    f"""SELECT r.*,f.nome funcionario,u.nome responsavel FROM reembolsos r
                       LEFT JOIN funcionarios f ON f.id=r.funcionario_id
                       LEFT JOIN usuarios u ON u.id=r.usuario_id WHERE {' AND '.join(reimbursement_where)}
                       ORDER BY r.data DESC,r.id DESC""", reimbursement_params,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/closings":
                closing_where, closing_params = ["obra_id=?"], [obra_id]
                if start: closing_where.append("periodo_fim>=?"); closing_params.append(start)
                if end: closing_where.append("periodo_inicio<=?"); closing_params.append(end)
                rows = db.execute(
                    f"SELECT * FROM fechamentos_pagamento WHERE {' AND '.join(closing_where)} ORDER BY periodo_fim DESC,id DESC",
                    closing_params,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path == "/api/payment-proposals":
                if not has_permission(user, "pagamentos"):
                    return self.send_json({"error":"Seu perfil não pode consultar propostas de pagamento"},403)
                if not self.require_work(db,user,obra_id): return
                return self.send_json(proposta_pagamento_store.list_proposals(db,obra_id,start,end))
            payment_proposal_export = re.fullmatch(r"/api/payment-proposals/(\d+)\.(pdf|xlsx|html)",path)
            if payment_proposal_export:
                if not has_permission(user,"pagamentos"):
                    return self.send_json({"error":"Seu perfil não pode consultar propostas de pagamento"},403)
                if payment_proposal_export.group(2) != "html" and not has_permission(user,"exportar") and user["perfil"] not in ("admin","engenharia","qualidade"):
                    return self.send_json({"error":"Seu perfil não pode exportar propostas"},403)
                proposal = proposta_pagamento_store.proposal_data(db,int(payment_proposal_export.group(1)))
                if not self.require_work(db,user,proposal["obra_id"]): return
                report = payment_proposal_report(db,proposal)
                filename = f"proposta-pagamento-{proposal['id']}"
                if payment_proposal_export.group(2) == "pdf":
                    return self.send_bytes(pdf_bytes(report),"application/pdf",filename+".pdf")
                if payment_proposal_export.group(2) == "xlsx":
                    return self.send_bytes(xlsx_bytes(report),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",filename+".xlsx")
                return self.send_bytes(print_html(report).encode("utf-8"),"text/html; charset=utf-8")
            payment_proposal_match = re.fullmatch(r"/api/payment-proposals/(\d+)",path)
            if payment_proposal_match:
                if not has_permission(user,"pagamentos"):
                    return self.send_json({"error":"Seu perfil não pode consultar propostas de pagamento"},403)
                proposal=proposta_pagamento_store.proposal_data(db,int(payment_proposal_match.group(1)))
                if not self.require_work(db,user,proposal["obra_id"]): return
                return self.send_json(proposal)
            if path.startswith("/api/closings/"):
                closing_id = int(path.rsplit("/",1)[-1])
                closing = db.execute("SELECT * FROM fechamentos_pagamento WHERE id=?", (closing_id,)).fetchone()
                if not closing: return self.send_json({"error":"Fechamento não encontrado"},404)
                if not self.require_work(db, user, closing["obra_id"]): return
                items = db.execute(
                    """SELECT i.*,f.nome funcionario,f.profissao,s.nome_interno servico,l.data,l.casa,l.bloco,l.ap
                       FROM itens_pagamento i JOIN funcionarios f ON f.id=i.funcionario_id
                       JOIN servicos s ON s.id=i.servico_id JOIN lancamentos l ON l.id=i.lancamento_id
                       WHERE i.fechamento_id=? ORDER BY f.nome,s.nome_interno,l.data""", (closing_id,),
                ).fetchall()
                discounts = db.execute("SELECT d.*,f.nome funcionario FROM descontos_pagamento d JOIN funcionarios f ON f.id=d.funcionario_id WHERE d.fechamento_id=? AND d.ativo=1", (closing_id,)).fetchall()
                extras = db.execute("SELECT e.*,f.nome funcionario FROM extras_pagamento e JOIN funcionarios f ON f.id=e.funcionario_id WHERE e.fechamento_id=? AND e.ativo=1", (closing_id,)).fetchall()
                return self.send_json({"closing":dict(closing),"items":[dict(x) for x in items],"discounts":[dict(x) for x in discounts],"extras":[dict(x) for x in extras]})
            if path == "/api/audit":
                if user["perfil"] != "admin":
                    return self.send_json({"error":"Auditoria exige perfil administrador"},403)
                audit_where, audit_params = [], []
                if start: audit_where.append("date(a.criado_em)>=?"); audit_params.append(start)
                if end: audit_where.append("date(a.criado_em)<=?"); audit_params.append(end)
                where_sql = " WHERE " + " AND ".join(audit_where) if audit_where else ""
                rows = db.execute(
                    """SELECT a.*,u.nome usuario FROM auditoria a LEFT JOIN usuarios u ON u.id=a.usuario_id"""
                    + where_sql + " ORDER BY a.id DESC LIMIT 500", audit_params,
                ).fetchall()
                return self.send_json([dict(x) for x in rows])
            if path.startswith("/api/reports/"):
                parts = path.split("/")[-1].rsplit(".", 1)
                if len(parts) != 2:
                    return self.send_json({"error":"Formato de relatório inválido"},400)
                report_type, output_format = parts
                try:
                    report = report_data(db, report_type, query)
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
                safe_name = (f"fechamento-{query['fechamento_id'][0]}" if report_type == "pagamento" and query.get("fechamento_id")
                             else "custos-producao" if report_type == "pagamento"
                             else "producao-pms" if report_type == "pms"
                             else f"{report_type}-pms")
                if output_format == "pdf":
                    if not has_permission(user, "exportar") and user["perfil"] not in ("admin","engenharia","qualidade"):
                        return self.send_json({"error":"Seu perfil não pode exportar PDF"},403)
                    return self.send_bytes(pdf_bytes(report), "application/pdf", safe_name+".pdf")
                if output_format == "html":
                    return self.send_bytes(print_html(report).encode("utf-8"), "text/html; charset=utf-8")
                if output_format == "xlsx":
                    if not has_permission(user, "exportar") and user["perfil"] not in ("admin","engenharia","qualidade"):
                        return self.send_json({"error":"Seu perfil não pode exportar Excel"},403)
                    return self.send_bytes(
                        xlsx_bytes(report),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        safe_name+".xlsx",
                    )
                return self.send_json({"error":"Formato não suportado"},400)
            if path == "/api/report.csv":
                if user["role"] != "admin":
                    return self.send_json({"error": "A exportação exige acesso de administrador"}, 403)
                csv_where, csv_params = ["l.obra_id=?", "l.ativo=1"], [obra_id]
                if start: csv_where.append("l.data>=?"); csv_params.append(start)
                if end: csv_where.append("l.data<=?"); csv_params.append(end)
                rows = db.execute(f"SELECT l.data,f.nome,s.nome_interno,l.tipo,l.quantidade_m2,l.horas_trabalhadas,l.extra,l.desconto,l.valor_pagar,l.valor_receber,l.bloco,l.ap,l.casa,l.obs FROM lancamentos l JOIN funcionarios f ON f.id=l.funcionario_id JOIN servicos s ON s.id=l.servico_id WHERE {' AND '.join(csv_where)} ORDER BY l.data", csv_params).fetchall()
                output = io.StringIO(); writer = csv.writer(output, delimiter=";")
                writer.writerow(rows[0].keys() if rows else ["sem_dados"]); writer.writerows([tuple(x) for x in rows])
                body = output.getvalue().encode("utf-8-sig")
                self.send_response(200); self.send_header("Content-Type", "text/csv; charset=utf-8"); self.send_header("Content-Disposition", "attachment; filename=relatorio-pms.csv"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        return self.send_json({"error": "Rota não encontrada"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/apontamento/catalog-preview":
                size = int(self.headers.get("Content-Length", 0))
                if size <= 0 or size > 4 * 1024 * 1024:
                    raise ValueError("Envie uma planilha .xlsx de até 4 MB")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                    raise ValueError("Envie um arquivo Excel .xlsx")
                payload = {"xlsx_bytes": self.rfile.read(size)}
            elif parsed.path == "/api/settings/restore":
                size = int(self.headers.get("Content-Length", 0))
                if size <= 0 or size > 128 * 1024 * 1024:
                    raise ValueError("O arquivo de backup está vazio ou excede 128 MB")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/vnd.sqlite3":
                    raise ValueError("Envie um arquivo SQLite de backup do sistema O gestor de Campo")
                payload = {"backup_bytes": self.rfile.read(size),
                           "confirmation": self.headers.get("X-Restore-Confirmation", "")}
            else:
                payload = self.read_json()
            with connect() as db:
                if parsed.path == "/api/auth/login":
                    token, csrf, user = authenticate(db, payload.get("username", ""), payload.get("password", ""), self.client_address[0])
                    secure = "; Secure" if SECURE_COOKIE else ""
                    cookie = f"pms_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800{secure}"
                    return self.send_json({"user": user, "csrf_token": csrf}, 200, {"Set-Cookie": cookie})
                user = self.require_user(db, csrf=True)
                if not user:
                    return
                if parsed.path == "/api/auth/logout":
                    token = self.raw_session_token()
                    if token:
                        audit(db, "sessao", user["sessao_id"], "logout", user["id"], ip=self.client_address[0])
                        db.execute("DELETE FROM sessoes WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                        db.commit()
                    return self.send_json({"ok": True}, 200, {"Set-Cookie": "pms_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                if parsed.path == "/api/auth/change-password":
                    change_password(db, user["id"], payload.get("current_password", ""), payload.get("new_password", ""), self.client_address[0])
                    return self.send_json({"ok": True, "reauthenticate": True})
                if parsed.path == "/api/auth/logout-others":
                    current = self.raw_session_token()
                    if not current:
                        return self.send_json({"error": "Sessão atual não encontrada"}, 401)
                    token_hash = hashlib.sha256(current.encode()).hexdigest()
                    removed = db.execute(
                        "DELETE FROM sessoes WHERE usuario_id=? AND token_hash<>?", (user["id"], token_hash)
                    ).rowcount
                    audit(db, "sessao", user["id"], "outras_sessoes_encerradas", user["id"],
                          after={"sessoes_encerradas": removed}, ip=self.client_address[0])
                    db.commit()
                    return self.send_json({"ok": True, "encerradas": removed})
                if parsed.path == "/api/settings/restore":
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Restauração exige perfil administrador"}, 403)
                    result = settings_store.restore_database(
                        db, payload["backup_bytes"], payload["confirmation"], user["id"], self.client_address[0]
                    )
                    return self.send_json({**result, "reauthenticate": True})
                if parsed.path == "/api/apontamento/catalog-preview":
                    if not has_permission(user, "lancamentos"):
                        return self.send_json({"error": "Seu perfil não possui acesso ao apontamento de campo"}, 403)
                    query = parse_qs(parsed.query)
                    try:
                        work_id = int(query.get("obra_id", [1])[0])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("Obra inválida") from exc
                    if not self.require_work(db, user, work_id):
                        return
                    return self.send_json({"rows": parse_catalog_xlsx(payload["xlsx_bytes"], query.get("kind", [""])[0])})
                if parsed.path == "/api/apontamento/sync":
                    if not has_permission(user, "lancamentos"):
                        return self.send_json({"error": "Seu perfil não possui acesso ao apontamento de campo"}, 403)
                    try:
                        obra_id = int(parse_qs(parsed.query).get("obra_id", [1])[0])
                    except (TypeError, ValueError):
                        raise ValueError("Obra inválida")
                    if not self.require_work(db, user, obra_id):
                        return
                    for item in payload.get("appointments", []):
                        settings_store.validate_future_date(db, obra_id, item.get("date", ""))
                    # A regra administrativa vem do PMS; nunca aceite que um cliente offline a sobrescreva.
                    safe_payload = {key: value for key, value in payload.items() if key != "settings"}
                    settings_store.ensure_field_catalog(db, obra_id, user["id"])
                    result = merge_apontamento_payload(db, obra_id, safe_payload, user["id"], self.client_address[0])
                    result["snapshot"]["settings"] = [settings_store.sync_field_settings(db, obra_id, user["id"], persist=False)]
                    return self.send_json(result)
                if parsed.path == "/api/settings/field-import":
                    if not has_permission(user, "lancamentos"):
                        return self.send_json({"error": "Seu perfil não pode importar apontamentos"}, 403)
                    try:
                        work_id = int(parse_qs(parsed.query).get("obra_id", [1])[0])
                    except (TypeError, ValueError):
                        raise ValueError("Obra inválida")
                    if not self.require_work(db, user, work_id):
                        return
                    result = settings_store.import_field_appointment(
                        db, work_id, payload.get("apontamento_id"), user["id"], self.client_address[0]
                    )
                    return self.send_json(result, 201)
                if parsed.path == "/api/users":
                    if user["role"] != "admin":
                        return self.send_json({"error": "Esta operação exige acesso de administrador"}, 403)
                    username = (payload.get("username") or "").strip().lower()
                    if not username or not payload.get("nome"):
                        raise ValueError("Nome e usuário são obrigatórios")
                    validate_password(payload.get("password", ""))
                    profile = payload.get("perfil") or payload.get("role") or "operador"
                    if profile not in PROFILES:
                        raise ValueError("Perfil de acesso inválido")
                    legacy_role = "admin" if profile == "admin" else "usuario"
                    display_name = str(payload["nome"]).strip().upper()
                    cursor = db.execute("INSERT INTO usuarios(nome,username,email,password_hash,role,perfil,deve_trocar_senha) VALUES(?,?,?,?,?,?,1)", (display_name, username, payload.get("email"), hash_password(payload["password"]), legacy_role, profile))
                    work_ids = payload.get("obra_ids") or [payload.get("obra_id", 1)]
                    if profile != "admin":
                        for work_id in {int(value) for value in work_ids if value is not None}:
                            if not db.execute("SELECT 1 FROM obras WHERE id=?", (work_id,)).fetchone():
                                raise ValueError("Obra informada para o usuário não existe")
                            db.execute("INSERT INTO usuario_obras(usuario_id,obra_id) VALUES(?,?)", (cursor.lastrowid, work_id))
                    audit(db, "usuario", cursor.lastrowid, "criado", user["id"], after={"nome":display_name,"username":username,"perfil":profile,"obra_ids":work_ids}, ip=self.client_address[0])
                    db.commit(); return self.send_json({"id": cursor.lastrowid}, 201)
                if parsed.path.startswith("/api/users/"):
                    if user["role"] != "admin":
                        return self.send_json({"error": "Esta operação exige acesso de administrador"}, 403)
                    parts = parsed.path.strip("/").split("/")
                    target_id = int(parts[2])
                    action = parts[3] if len(parts) > 3 else ""
                    if target_id == user["id"] and action == "toggle":
                        raise ValueError("Você não pode desativar sua própria conta")
                    before = db.execute("SELECT id,nome,username,perfil,ativo,deve_trocar_senha FROM usuarios WHERE id=?", (target_id,)).fetchone()
                    if not before:
                        return self.send_json({"error": "Usuário não encontrado"}, 404)
                    if action == "toggle":
                        db.execute("UPDATE usuarios SET ativo=CASE ativo WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (target_id,))
                        db.execute("DELETE FROM sessoes WHERE usuario_id=?", (target_id,))
                    elif action == "reset-password":
                        validate_password(payload.get("password", ""))
                        db.execute("UPDATE usuarios SET password_hash=?,deve_trocar_senha=1 WHERE id=?", (hash_password(payload["password"]), target_id))
                        db.execute("DELETE FROM sessoes WHERE usuario_id=?", (target_id,))
                    else:
                        raise ValueError("Ação de usuário inválida")
                    after = db.execute("SELECT id,nome,username,perfil,ativo,deve_trocar_senha FROM usuarios WHERE id=?", (target_id,)).fetchone()
                    audit(db, "usuario", target_id, action, user["id"], dict(before), dict(after), self.client_address[0])
                    db.commit(); return self.send_json({"ok": True})
                if parsed.path == "/api/entries":
                    if not has_permission(user, "lancamentos"):
                        return self.send_json({"error":"Seu perfil não pode criar lançamentos"},403)
                    work_id = int(payload.get("obra_id", 1))
                    if not self.require_work(db, user, work_id): return
                    settings_store.validate_future_date(db, work_id, payload.get("data", ""))
                    return self.send_json(create_entry(db, payload, user["id"], self.client_address[0]), 201)
                if parsed.path == "/api/appropriations":
                    if not has_permission(user, "apropriacoes"):
                        return self.send_json({"error":"Seu perfil não pode criar apropriações"},403)
                    if not self.require_work(db, user, int(payload.get("obra_id", 1))): return
                    return self.send_json(create_appropriation(db, payload, user["id"], self.client_address[0]), 201)
                if parsed.path == "/api/quality":
                    if not has_permission(user, "qualidade"):
                        return self.send_json({"error":"Seu perfil não pode avaliar qualidade"},403)
                    quality_work = db.execute("SELECT obra_id FROM lancamentos WHERE id=? AND ativo=1", (int(payload["lancamento_id"]),)).fetchone()
                    if not quality_work:
                        return self.send_json({"error": "Lançamento não encontrado"}, 404)
                    if not self.require_work(db, user, quality_work["obra_id"]): return
                    return self.send_json(save_quality(db, int(payload["lancamento_id"]), payload.get("situacao","nao_avaliado"), payload.get("observacao"), user["id"], self.client_address[0]))
                if parsed.path == "/api/closings":
                    if not has_permission(user, "pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode gerar fechamentos"},403)
                    work_id = int(payload.get("obra_id",1))
                    if not self.require_work(db, user, work_id): return
                    result = generate_closing(db, work_id, payload["periodo_inicio"], payload["periodo_fim"], payload.get("pms_numero"), user["id"], self.client_address[0])
                    return self.send_json(result,201)
                if parsed.path == "/api/payment-proposals/generate":
                    if not has_permission(user,"pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode gerar propostas de pagamento"},403)
                    work_id=int(payload.get("obra_id",1))
                    if not self.require_work(db,user,work_id): return
                    result=proposta_pagamento_store.create_from_entries(
                        db,work_id,payload.get("periodo_inicio"),payload.get("periodo_fim"),
                        payload.get("pms_numero"),user["id"],self.client_address[0])
                    return self.send_json(result,201)
                payment_proposal_items=re.fullmatch(r"/api/payment-proposals/(\d+)/items",parsed.path)
                if payment_proposal_items:
                    if not has_permission(user,"pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de pagamento"},403)
                    proposal=proposta_pagamento_store.proposal_data(db,int(payment_proposal_items.group(1)))
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    return self.send_json(proposta_pagamento_store.add_item(db,proposal["id"],payload,user["id"],self.client_address[0]),201)
                if parsed.path == "/api/payment-adjustments":
                    if not has_permission(user, "ajustes"):
                        return self.send_json({"error":"Seu perfil não pode ajustar pagamentos"},403)
                    item_work = db.execute("""SELECT f.obra_id FROM itens_pagamento i JOIN fechamentos_pagamento f ON f.id=i.fechamento_id WHERE i.id=?""", (int(payload["item_id"]),)).fetchone()
                    if not item_work:
                        return self.send_json({"error": "Item de pagamento não encontrado"}, 404)
                    if not self.require_work(db, user, item_work["obra_id"]): return
                    return self.send_json(adjust_payment_item(db, int(payload["item_id"]), payload["valor_novo"], payload.get("justificativa"), user["id"], self.client_address[0]))
                if parsed.path in ("/api/payment-discounts","/api/payment-extras"):
                    if not has_permission(user, "ajustes"):
                        return self.send_json({"error":"Seu perfil não pode alterar o fechamento"},403)
                    component_work = db.execute("SELECT obra_id FROM fechamentos_pagamento WHERE id=?", (int(payload["fechamento_id"]),)).fetchone()
                    if not component_work:
                        return self.send_json({"error": "Fechamento não encontrado"}, 404)
                    if not self.require_work(db, user, component_work["obra_id"]): return
                    kind = "desconto" if parsed.path.endswith("discounts") else "extra"
                    return self.send_json(payment_component(db, kind, payload, user["id"], self.client_address[0]),201)
                if parsed.path == "/api/reimbursements":
                    if not has_permission(user, "pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode lançar reembolsos"},403)
                    if not self.require_work(db, user, int(payload.get("obra_id", 1))): return
                    fields = ("obra_id","pms_numero","funcionario_id","data","descricao","valor","status","usuario_id")
                    values = [payload.get(key) for key in fields[:-1]] + [user["id"]]
                    cursor = db.execute(f"INSERT INTO reembolsos({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", values)
                    audit(db,"reembolso",cursor.lastrowid,"criado",user["id"],after=payload,ip=self.client_address[0])
                    db.commit(); return self.send_json({"id":cursor.lastrowid},201)
                if parsed.path == "/api/pms/generate":
                    if not has_permission(user, "pms"):
                        return self.send_json({"error":"Seu perfil não pode gerar PMS"},403)
                    obra = int(payload.get("obra_id",1)); number = int(payload["numero"])
                    if not self.require_work(db, user, obra): return
                    existing_measurement = db.execute("SELECT * FROM medicoes_pms WHERE obra_id=? AND numero=?", (obra, number)).fetchone()
                    if existing_measurement and existing_measurement["status"] != "rascunho":
                        raise ValueError("Medição já saiu do rascunho; utilize reabertura/retificação quando esse fluxo estiver autorizado")
                    totals = dashboard(db, obra, payload["periodo_inicio"], payload["periodo_fim"])
                    discount = db.execute("SELECT COALESCE(SUM(valor_desconto),0) FROM descontos_servico WHERE obra_id=?", (obra,)).fetchone()[0]
                    cursor = db.execute(
                        """INSERT INTO medicoes_pms(obra_id,numero,periodo_inicio,periodo_fim,status,valor_bruto,valor_desconto,valor_liquido,observacao)
                           VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(obra_id,numero) DO UPDATE SET periodo_inicio=excluded.periodo_inicio,periodo_fim=excluded.periodo_fim,
                           status=excluded.status,valor_bruto=excluded.valor_bruto,valor_desconto=excluded.valor_desconto,
                           valor_liquido=excluded.valor_liquido,observacao=excluded.observacao RETURNING id""",
                        (obra,number,payload["periodo_inicio"],payload["periodo_fim"],payload.get("status","rascunho"),totals["total_receber"],discount,totals["total_receber"]-discount,payload.get("observacao")),
                    )
                    measurement_id = cursor.fetchone()[0]
                    db.execute("UPDATE lancamentos SET pms_numero=? WHERE obra_id=? AND data BETWEEN ? AND ?",(number,obra,payload["periodo_inicio"],payload["periodo_fim"]))
                    audit(db,"medicao_pms",measurement_id,"gerada",user["id"],after=payload,ip=self.client_address[0])
                    db.commit(); return self.send_json({"id":measurement_id,"totais":totals},201)
                if parsed.path == "/api/proposals/generate":
                    if not has_permission(user, "pms"):
                        return self.send_json({"error":"Seu perfil não pode gerar propostas de medição"},403)
                    obra = int(payload.get("obra_id", 0))
                    if not self.require_work(db, user, obra): return
                    return self.send_json(proposta_store.create_from_entries(
                        db, obra, payload.get("periodo_inicio"), payload.get("periodo_fim"), payload.get("data_proposta"),
                        payload.get("pms_numero"), user["id"], self.client_address[0]), 201)
                reorder_match = re.fullmatch(r"/api/proposals/(\d+)/reorder", parsed.path)
                if reorder_match:
                    proposal_id=int(reorder_match.group(1)); proposal=proposta_store.proposal_data(db,proposal_id)
                    if not has_permission(user,"pms"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de medição"},403)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    return self.send_json(proposta_store.reorder_services(db,proposal_id,payload.get("servicos",[]),user["id"],self.client_address[0]))
                proposal_service = re.fullmatch(r"/api/proposals/(\d+)/services", parsed.path)
                proposal_item = re.fullmatch(r"/api/proposals/(\d+)/services/(\d+)/items", parsed.path)
                if proposal_service or proposal_item:
                    proposal_id = int((proposal_service or proposal_item).group(1)); proposal = proposta_store.proposal_data(db, proposal_id)
                    if not has_permission(user, "pms"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de medição"},403)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    if proposal_service:
                        return self.send_json(proposta_store.add_service(db,proposal_id,payload.get("nome_servico"),user["id"],self.client_address[0]),201)
                    return self.send_json(proposta_store.add_item(db,proposal_id,int(proposal_item.group(2)),payload.get("bloco"),payload.get("casa"),user["id"],self.client_address[0]),201)
                if parsed.path == "/api/employees":
                    if user["role"] != "admin":
                        return self.send_json({"error": "Cadastro de funcionários exige acesso de administrador"}, 403)
                    payload["nome"] = str(payload.get("nome") or "").strip().upper()
                    fields = ("obra_id","nome","profissao","situacao","telefone","documento","data_admissao","data_desligamento","tipo_vinculo","observacoes")
                    cursor = db.execute(
                        f"INSERT INTO funcionarios({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                        [payload.get(key) if key != "obra_id" else payload.get("obra_id",1) for key in fields],
                    )
                    audit(db,"funcionario",cursor.lastrowid,"criado",user["id"],after=payload,ip=self.client_address[0])
                    db.commit(); return self.send_json({"id":cursor.lastrowid},201)
                if parsed.path == "/api/services":
                    if user["role"] != "admin":
                        return self.send_json({"error": "Cadastro de serviços exige acesso de administrador"}, 403)
                    payload["nome_interno"] = str(payload["nome_interno"]).strip().upper()
                    cursor = db.execute("INSERT INTO servicos(obra_id,codigo,nome_interno,descricao_pms,categoria,preco_pagamento,unidade,quantidade_padrao,valor_receber_unitario) VALUES(?,?,?,?,?,?,?,?,?)", (payload.get("obra_id",1),payload.get("codigo"),payload["nome_interno"],payload.get("descricao_pms"),payload.get("categoria"),payload.get("preco_pagamento",0),payload.get("unidade"),payload.get("quantidade_padrao",1),payload.get("valor_receber_unitario",0)))
                    audit(db,"servico",cursor.lastrowid,"criado",user["id"],after=payload,ip=self.client_address[0])
                    service_id = cursor.lastrowid
                    create_service_price(db, {
                        "servico_id": service_id, "obra_id": payload.get("obra_id",1),
                        "preco_pagamento": payload.get("preco_pagamento",0),
                        "valor_receber_unitario": payload.get("valor_receber_unitario",0),
                        "inicio_vigencia": payload.get("inicio_vigencia") or date.today().isoformat(),
                        "motivo": payload.get("motivo_preco") or "Preço inicial do serviço",
                    }, user["id"], self.client_address[0])
                    db.commit(); return self.send_json({"id":service_id},201)
                if parsed.path == "/api/service-prices":
                    if not has_permission(user, "servico.preco.alterar"):
                        return self.send_json({"error": "Seu perfil não pode alterar preços"}, 403)
                    if not self.require_work(db, user, int(payload.get("obra_id", 1))): return
                    return self.send_json(create_service_price(db, payload, user["id"], self.client_address[0]), 201)
                if parsed.path == "/api/employee-service-prices":
                    if not has_permission(user, "servico.preco.alterar"):
                        return self.send_json({"error": "Seu perfil não pode alterar preços específicos"}, 403)
                    if not self.require_work(db, user, int(payload.get("obra_id", 1))): return
                    result = create_employee_service_price(db, payload, user["id"], self.client_address[0])
                    return self.send_json(result, 201 if result["criado"] else 200)
        except sqlite3.IntegrityError:
            return self.send_json({"error": "Já existe um cadastro com esses dados"}, 409)
        except PermissionError as exc:
            return self.send_json({"error": str(exc)}, 403)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            return self.send_json({"error": f"Não foi possível salvar: {exc}"}, 500)
        return self.send_json({"error": "Rota não encontrada"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            with connect() as db:
                user = self.require_user(db, csrf=True)
                if not user:
                    return
                service_match = re.fullmatch(r"/api/proposals/(\d+)/services/(\d+)", parsed.path)
                item_match = re.fullmatch(r"/api/proposals/(\d+)/items/(\d+)", parsed.path)
                if service_match or item_match:
                    proposal_id = int((service_match or item_match).group(1)); proposal = proposta_store.proposal_data(db, proposal_id)
                    if not has_permission(user,"pms"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de medição"},403)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    if service_match: return self.send_json(proposta_store.update_service(db,proposal_id,int(service_match.group(2)),payload,user["id"],self.client_address[0]))
                    return self.send_json(proposta_store.update_item(db,proposal_id,int(item_match.group(2)),payload,user["id"],self.client_address[0]))
                payment_item_match=re.fullmatch(r"/api/payment-proposals/(\d+)/items/(\d+)",parsed.path)
                if payment_item_match:
                    if not has_permission(user,"pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de pagamento"},403)
                    proposal_id,item_id=map(int,payment_item_match.groups())
                    proposal=proposta_pagamento_store.proposal_data(db,proposal_id)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    return self.send_json(proposta_pagamento_store.update_item(db,proposal_id,item_id,payload,user["id"],self.client_address[0]))
                block_match = re.fullmatch(r"/api/proposals/(\d+)/blocks", parsed.path)
                if block_match:
                    proposal_id=int(block_match.group(1)); proposal=proposta_store.proposal_data(db,proposal_id)
                    if not has_permission(user,"pms"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de medição"},403)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    return self.send_json(proposta_store.update_block(db,proposal_id,payload.get("item_ids",[]),payload.get("bloco"),user["id"],self.client_address[0]))
                parts = parsed.path.strip("/").split("/")
                if parts == ["api", "settings", "company"]:
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Configuração da empresa exige administrador"}, 403)
                    return self.send_json(settings_store.update_company(db, payload, user["id"], self.client_address[0]))
                if parts == ["api", "settings", "work"]:
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Configuração da obra exige administrador"}, 403)
                    try:
                        work_id = int(parse_qs(parsed.query).get("obra_id", [1])[0])
                    except (TypeError, ValueError):
                        raise ValueError("Obra inválida")
                    if not db.execute("SELECT 1 FROM obras WHERE id=?", (work_id,)).fetchone():
                        return self.send_json({"error": "Obra não encontrada"}, 404)
                    return self.send_json(settings_store.update_work_settings(db, work_id, payload, user["id"], self.client_address[0]))
                if parts == ["api", "settings", "preferences"]:
                    return self.send_json(settings_store.update_preferences(db, payload, user, self.client_address[0]))
                if parts == ["api", "settings", "account"]:
                    return self.send_json(settings_store.update_account(db, payload, user, self.client_address[0]))
                if parts == ["api", "settings", "field-links"]:
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Vínculos do apontamento exigem administrador"}, 403)
                    try:
                        work_id = int(parse_qs(parsed.query).get("obra_id", [1])[0])
                    except (TypeError, ValueError):
                        raise ValueError("Obra inválida")
                    if not self.require_work(db, user, work_id):
                        return
                    return self.send_json(settings_store.save_field_link(
                        db, work_id, payload.get("tipo"), payload.get("id_origem"),
                        payload.get("id_destino"), user["id"], self.client_address[0]
                    ))
                if len(parts) == 3 and parts[:2] == ["api","entries"]:
                    if not has_permission(user, "lancamentos"):
                        return self.send_json({"error":"Seu perfil não pode editar lançamentos"},403)
                    entry_work = db.execute("SELECT obra_id,data FROM lancamentos WHERE id=?", (int(parts[2]),)).fetchone()
                    if not entry_work:
                        return self.send_json({"error": "Lançamento não encontrado"}, 404)
                    if not self.require_work(db, user, entry_work["obra_id"]): return
                    settings_store.validate_future_date(db, entry_work["obra_id"], payload.get("data", entry_work["data"]))
                    allow_retification = bool(payload.get("retificar")) and has_permission(user, "administrativo.retificar")
                    return self.send_json(update_entry(db, int(parts[2]), payload, user["id"], self.client_address[0], allow_retification))
                if len(parts) == 3 and parts[:2] == ["api", "service-prices"]:
                    if not has_permission(user, "servico.preco.alterar"):
                        return self.send_json({"error": "Seu perfil não pode corrigir preços"}, 403)
                    price = db.execute("SELECT obra_id FROM servico_precos WHERE id=?", (int(parts[2]),)).fetchone()
                    if not price:
                        return self.send_json({"error": "Preço não encontrado"}, 404)
                    if not self.require_work(db, user, price["obra_id"]): return
                    return self.send_json(correct_service_price(db, int(parts[2]), payload, user["id"], self.client_address[0]))
                if len(parts) == 3 and parts[:2] == ["api", "works"]:
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Edição de obra exige administrador"}, 403)
                    target_id = int(parts[2])
                    before = db.execute("SELECT * FROM obras WHERE id=?", (target_id,)).fetchone()
                    if not before: return self.send_json({"error": "Obra não encontrada"}, 404)
                    allowed = ("codigo","nome","cliente_contratante","cnpj_contratante","endereco","cidade","estado","data_inicio","previsao_termino","engenheiro_responsavel","encarregado","status","observacoes")
                    changes = {key: payload[key] for key in allowed if key in payload}
                    for key in ("nome", "cliente_contratante", "engenheiro_responsavel", "encarregado"):
                        if key in changes and isinstance(changes[key], str):
                            changes[key] = changes[key].strip().upper()
                    if not changes: raise ValueError("Nenhum campo válido para atualizar")
                    cursor = db.execute(
                        f"UPDATE obras SET {','.join(key+'=?' for key in changes)},versao=versao+1,atualizado_em=CURRENT_TIMESTAMP WHERE id=? AND versao=?",
                        [*changes.values(), target_id, int(payload.get("versao", before["versao"]))],
                    )
                    if cursor.rowcount != 1: raise ValueError("Esta obra foi alterada por outro usuário. Recarregue a página.")
                    after = dict(db.execute("SELECT * FROM obras WHERE id=?", (target_id,)).fetchone())
                    audit(db,"obras",target_id,"alterada",user["id"],dict(before),after,self.client_address[0])
                    db.commit(); return self.send_json(after)
                if len(parts) == 3 and parts[:2] == ["api", "users"]:
                    if user["perfil"] != "admin":
                        return self.send_json({"error": "Edição de usuário exige administrador"}, 403)
                    target_id = int(parts[2])
                    before = db.execute("SELECT * FROM usuarios WHERE id=?", (target_id,)).fetchone()
                    if not before: return self.send_json({"error": "Usuário não encontrado"}, 404)
                    profile = payload.get("perfil", before["perfil"])
                    if profile not in PROFILES: raise ValueError("Perfil de acesso inválido")
                    active = int(payload.get("ativo", before["ativo"]))
                    if target_id == user["id"] and not active: raise ValueError("Você não pode desativar sua própria conta")
                    cursor = db.execute(
                        """UPDATE usuarios SET nome=?,email=?,perfil=?,role=?,ativo=?,versao=versao+1,
                           atualizado_em=CURRENT_TIMESTAMP WHERE id=? AND versao=?""",
                        (str(payload.get("nome",before["nome"])).strip().upper(),payload.get("email",before["email"]),profile,
                         "admin" if profile=="admin" else "usuario",active,target_id,int(payload.get("versao",before["versao"]))),
                    )
                    if cursor.rowcount != 1: raise ValueError("Este usuário foi alterado por outro administrador. Recarregue a página.")
                    if profile == "admin":
                        db.execute("DELETE FROM usuario_obras WHERE usuario_id=?", (target_id,))
                    elif "obra_ids" in payload:
                        work_ids = {int(value) for value in payload["obra_ids"]}
                        if not work_ids: raise ValueError("Selecione ao menos uma obra para o usuário")
                        db.execute("DELETE FROM usuario_obras WHERE usuario_id=?", (target_id,))
                        for work_id in work_ids:
                            if not db.execute("SELECT 1 FROM obras WHERE id=?", (work_id,)).fetchone(): raise ValueError("Obra informada não existe")
                            db.execute("INSERT INTO usuario_obras(usuario_id,obra_id) VALUES(?,?)", (target_id,work_id))
                    after = dict(db.execute("SELECT * FROM usuarios WHERE id=?", (target_id,)).fetchone())
                    audit(db,"usuario",target_id,"alterado",user["id"],dict(before),after,self.client_address[0])
                    db.commit(); return self.send_json(public_user(after))
                if len(parts) == 3 and parts[:2] == ["api", "appropriations"]:
                    if not has_permission(user, "apropriacoes"):
                        return self.send_json({"error": "Seu perfil não pode editar apropriações"}, 403)
                    target_id = int(parts[2])
                    before = db.execute("SELECT * FROM apropriacoes WHERE id=?", (target_id,)).fetchone()
                    if not before: return self.send_json({"error": "Apropriação não encontrada"}, 404)
                    if not self.require_work(db, user, before["obra_id"]): return
                    if before["status"] != "aberta" and user["perfil"] != "admin":
                        return self.send_json({"error": "Apropriação fechada exige retificação administrativa"}, 403)
                    if before["status"] != "aberta" and not (payload.get("motivo_alteracao") or "").strip():
                        raise ValueError("O motivo da retificação é obrigatório")
                    employee_id = int(payload.get("funcionario_id", before["funcionario_id"]))
                    service_id = int(payload.get("servico_id", before["servico_id"]))
                    if not db.execute("SELECT 1 FROM funcionarios WHERE id=? AND obra_id=? AND ativo=1", (employee_id,before["obra_id"])).fetchone(): raise ValueError("Colaborador não pertence à obra")
                    if not db.execute("SELECT 1 FROM servicos WHERE id=? AND obra_id=? AND ativo=1", (service_id,before["obra_id"])).fetchone(): raise ValueError("Serviço não pertence à obra")
                    allowed = ("data","funcionario_id","servico_id","pms_numero","inicio_manha","termino_manha","inicio_tarde","termino_tarde","assinatura_encarregado","observacao","status")
                    changes = {key:payload[key] for key in allowed if key in payload}
                    if not changes: raise ValueError("Nenhum campo válido para atualizar")
                    cursor = db.execute(f"UPDATE apropriacoes SET {','.join(key+'=?' for key in changes)},atualizado_por=?,atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=? AND versao=?",[*changes.values(),user["id"],target_id,int(payload.get("versao",before["versao"]))])
                    if cursor.rowcount != 1: raise ValueError("Esta apropriação foi alterada por outro usuário. Recarregue a página.")
                    after = dict(db.execute("SELECT * FROM apropriacoes WHERE id=?",(target_id,)).fetchone())
                    audit(db,"apropriacao",target_id,"retificada" if before["status"]!="aberta" else "alterada",user["id"],dict(before),after,self.client_address[0],details={"motivo":payload.get("motivo_alteracao")})
                    db.commit(); return self.send_json(after)
                if len(parts) == 3 and parts[:2] == ["api", "reimbursements"]:
                    if not has_permission(user, "pagamentos"):
                        return self.send_json({"error": "Seu perfil não pode editar reembolsos"}, 403)
                    target_id = int(parts[2])
                    before = db.execute("SELECT * FROM reembolsos WHERE id=?", (target_id,)).fetchone()
                    if not before: return self.send_json({"error": "Reembolso não encontrado"}, 404)
                    if not self.require_work(db, user, before["obra_id"]): return
                    value = float(payload.get("valor", before["valor"]))
                    if value < 0: raise ValueError("O valor não pode ser negativo")
                    critical = before["status"] in ("aprovado","reembolsado") or value != float(before["valor"])
                    if critical and not (payload.get("motivo_alteracao") or "").strip(): raise ValueError("O motivo da alteração financeira é obrigatório")
                    employee_id = payload.get("funcionario_id", before["funcionario_id"])
                    if employee_id and not db.execute("SELECT 1 FROM funcionarios WHERE id=? AND obra_id=?", (employee_id,before["obra_id"])).fetchone(): raise ValueError("Colaborador não pertence à obra")
                    allowed = ("pms_numero","funcionario_id","data","descricao","valor","status")
                    changes = {key:payload[key] for key in allowed if key in payload}
                    if not changes: raise ValueError("Nenhum campo válido para atualizar")
                    cursor = db.execute(f"UPDATE reembolsos SET {','.join(key+'=?' for key in changes)},atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=? AND versao=?",[*changes.values(),target_id,int(payload.get("versao",before["versao"]))])
                    if cursor.rowcount != 1: raise ValueError("Este reembolso foi alterado por outro usuário. Recarregue a página.")
                    after = dict(db.execute("SELECT * FROM reembolsos WHERE id=?",(target_id,)).fetchone())
                    audit(db,"reembolso",target_id,"alterado",user["id"],dict(before),after,self.client_address[0],details={"motivo":payload.get("motivo_alteracao")})
                    db.commit(); return self.send_json(after)
                if len(parts) == 3 and parts[:2] in (["api","employees"],["api","services"]):
                    if user["perfil"] != "admin":
                        return self.send_json({"error":"Cadastro mestre exige administrador"},403)
                    entity, target_id = parts[1], int(parts[2])
                    table = "funcionarios" if entity == "employees" else "servicos"
                    before = db.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
                    if not before: return self.send_json({"error":"Cadastro não encontrado"},404)
                    allowed = ("nome","profissao","situacao","telefone","documento","data_admissao","data_desligamento","tipo_vinculo","observacoes","ativo") if table=="funcionarios" else ("codigo","nome_interno","descricao_pms","categoria","unidade","quantidade_padrao","ativo")
                    changes = {key:payload[key] for key in allowed if key in payload}
                    if not changes: raise ValueError("Nenhum campo válido para atualizar")
                    if table == "servicos" and "unidade" in changes and changes["unidade"] != before["unidade"]:
                        used = db.execute("SELECT 1 FROM lancamentos WHERE servico_id=? LIMIT 1", (target_id,)).fetchone()
                        if used:
                            raise ValueError("A unidade não pode ser alterada porque existem lançamentos históricos")
                    cursor = db.execute(f"UPDATE {table} SET {','.join(key+'=?' for key in changes)},atualizado_em=CURRENT_TIMESTAMP,versao=versao+1 WHERE id=? AND versao=?",[*changes.values(),target_id,int(payload.get("versao",before["versao"]))])
                    if cursor.rowcount != 1: raise ValueError("Este cadastro foi alterado por outro usuário. Recarregue a página.")
                    after = dict(db.execute(f"SELECT * FROM {table} WHERE id=?",(target_id,)).fetchone())
                    audit(db,table,target_id,"alterado",user["id"],dict(before),after,self.client_address[0])
                    db.commit(); return self.send_json(after)
                if len(parts) == 3 and parts[:2] == ["api","forecast"]:
                    if user["perfil"] not in ("admin","engenharia","financeiro"):
                        return self.send_json({"error":"Seu perfil não pode editar previsão"},403)
                    target_id = int(parts[2])
                    before = db.execute("SELECT * FROM previsao_pms WHERE id=?",(target_id,)).fetchone()
                    if not before: return self.send_json({"error":"Item não encontrado"},404)
                    if not self.require_work(db, user, before["obra_id"]): return
                    reason = (payload.get("motivo_alteracao") or "").strip()
                    if not reason: raise ValueError("O motivo da alteração financeira é obrigatório")
                    quantity = float(payload.get("quantidade",before["quantidade"]))
                    unit = float(payload.get("valor_unitario",before["valor_unitario"]))
                    db.execute("UPDATE previsao_pms SET descricao_pms=?,valor_unitario=?,quantidade=?,valor_total=? WHERE id=?",(payload.get("descricao_pms",before["descricao_pms"]),unit,quantity,round(unit*quantity,2),target_id))
                    after = dict(db.execute("SELECT * FROM previsao_pms WHERE id=?",(target_id,)).fetchone())
                    audit(db,"previsao_pms",target_id,"alterada",user["id"],dict(before),after,self.client_address[0],details={"motivo":reason})
                    db.commit(); return self.send_json({"ok":True})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json({"error":str(exc)},400)
        except Exception as exc:
            return self.send_json({"error":f"Não foi possível atualizar: {exc}"},500)
        return self.send_json({"error":"Rota não encontrada"},404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_json() if int(self.headers.get("Content-Length", 0)) else {}
            with connect() as db:
                user = self.require_user(db, csrf=True)
                if not user:
                    return
                entry_match = re.fullmatch(r"/api/entries/(\d+)", parsed.path)
                if entry_match:
                    if user["perfil"] != "admin":
                        return self.send_json({"error":"Excluir lançamentos exige acesso de administrador"},403)
                    entry_id = int(entry_match.group(1))
                    entry = db.execute("SELECT obra_id FROM lancamentos WHERE id=?", (entry_id,)).fetchone()
                    if not entry:
                        return self.send_json({"error":"Lançamento não encontrado"},404)
                    if not self.require_work(db,user,entry["obra_id"]): return
                    return self.send_json(deactivate_entry(db,entry_id,payload.get("motivo"),user["id"],self.client_address[0]))
                service_match = re.fullmatch(r"/api/proposals/(\d+)/services/(\d+)", parsed.path)
                item_match = re.fullmatch(r"/api/proposals/(\d+)/items/(\d+)", parsed.path)
                if service_match or item_match:
                    proposal_id = int((service_match or item_match).group(1)); proposal = proposta_store.proposal_data(db, proposal_id)
                    if not has_permission(user,"pms"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de medição"},403)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    if service_match: return self.send_json(proposta_store.delete_service(db,proposal_id,int(service_match.group(2)),user["id"],self.client_address[0]))
                    return self.send_json(proposta_store.delete_item(db,proposal_id,int(item_match.group(2)),user["id"],self.client_address[0]))
                payment_item_match=re.fullmatch(r"/api/payment-proposals/(\d+)/items/(\d+)",parsed.path)
                if payment_item_match:
                    if not has_permission(user,"pagamentos"):
                        return self.send_json({"error":"Seu perfil não pode editar propostas de pagamento"},403)
                    proposal_id,item_id=map(int,payment_item_match.groups())
                    proposal=proposta_pagamento_store.proposal_data(db,proposal_id)
                    if not self.require_work(db,user,proposal["obra_id"]): return
                    return self.send_json(proposta_pagamento_store.remove_item(db,proposal_id,item_id,user["id"],self.client_address[0]))
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 3 and parts[:2] in (["api","payment-discounts"],["api","payment-extras"]):
                    if not has_permission(user, "ajustes"):
                        return self.send_json({"error":"Seu perfil não pode alterar o fechamento"},403)
                    kind = "desconto" if parts[1] == "payment-discounts" else "extra"
                    table = "descontos_pagamento" if kind == "desconto" else "extras_pagamento"
                    component = db.execute(
                        f"""SELECT f.obra_id FROM {table} c JOIN fechamentos_pagamento f ON f.id=c.fechamento_id WHERE c.id=?""",
                        (int(parts[2]),),
                    ).fetchone()
                    if not component:
                        return self.send_json({"error": "Registro financeiro não encontrado"}, 404)
                    if not self.require_work(db, user, component["obra_id"]): return
                    return self.send_json(remove_payment_component(db,kind,int(parts[2]),user["id"],self.client_address[0]))
                if len(parts) == 3 and parts[:2] in (["api","employees"],["api","services"]):
                    if user["perfil"] != "admin":
                        return self.send_json({"error":"Cadastro mestre exige administrador"},403)
                    table = "funcionarios" if parts[1]=="employees" else "servicos"
                    target_id = int(parts[2])
                    before = db.execute(f"SELECT * FROM {table} WHERE id=?",(target_id,)).fetchone()
                    if not before: return self.send_json({"error":"Cadastro não encontrado"},404)
                    db.execute(f"UPDATE {table} SET ativo=0,atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(target_id,))
                    audit(db,table,target_id,"desativado",user["id"],dict(before),{"ativo":0},self.client_address[0])
                    db.commit(); return self.send_json({"ok":True,"historico_preservado":True})
        except (ValueError, KeyError) as exc:
            return self.send_json({"error":str(exc)},400)
        except Exception as exc:
            return self.send_json({"error":f"Não foi possível remover: {exc}"},500)
        return self.send_json({"error":"Rota não encontrada"},404)


def main():
    result = init_db()
    with ThreadingHTTPServer((HOST, PORT), PMSHandler) as server:
        print(f"O gestor de Campo pronto em http://{HOST}:{PORT}")
        print(f"Banco: {result['database']}")
        server.serve_forever()


if __name__ == "__main__":
    main()
