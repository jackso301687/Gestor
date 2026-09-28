"""Integração das rotas HTTP em processo e banco temporário, sem abrir portas."""
import io
import json
import zipfile
from email.message import Message
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from server import PMSHandler


class HTTPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="pms-http-test-")
        cls.db_patch = patch.object(database, "DB_PATH", Path(cls.temp.name) / "test.sqlite3")
        cls.db_patch.start()
        database.init_db()
        # Senha exclusiva da fixture, sem alterar credenciais do banco operacional.
        with database.connect() as db:
            db.execute("UPDATE usuarios SET password_hash=?,deve_trocar_senha=0", (database.hash_password("TestePMS@2026"),))
            db.execute("DELETE FROM usuario_obras WHERE usuario_id=(SELECT id FROM usuarios WHERE username='usuario') AND obra_id<>1")
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db_patch.stop()
        cls.temp.cleanup()

    def request(self, method, path, payload=None, cookie=None, csrf=None):
        handler = object.__new__(PMSHandler)
        handler.path = path
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        if cookie:
            handler.headers["Cookie"] = cookie
        if csrf:
            handler.headers["X-CSRF-Token"] = csrf
        request_body = json.dumps(payload).encode() if payload is not None else b""
        handler.headers["Content-Length"] = str(len(request_body))
        handler.rfile = io.BytesIO(request_body)
        handler.wfile = io.BytesIO()
        response_headers = {}
        response_status = []
        handler.send_response = lambda status: response_status.append(status)
        handler.send_header = lambda name, value: response_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda status: response_status.append(status)
        getattr(handler, f"do_{method}")()
        body = handler.wfile.getvalue()
        parsed = json.loads(body) if "application/json" in response_headers.get("Content-Type", "") else body
        return response_status[-1], response_headers, parsed

    def login(self, username="admin"):
        status, headers, body = self.request("POST", "/api/auth/login", {"username": username, "password": "TestePMS@2026"})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";")[0], body["csrf_token"]

    def test_static_files_never_expose_database_or_source(self):
        for path in ("/pms.sqlite3", "/pms.sqlite3-wal", "/data/apontamento.sqlite3", "/database.py", "/server.py", "/schema.sql", "/.git/config", "/%2e%2e/server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        for path in ("/", "/campo/", "/campo/index.html?obra_id=1", "/campo/sw.js", "/tokens.css", "/local-launch.js"):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_sync_requires_session_csrf_and_authorized_work(self):
        self.assertEqual(self.request("GET", "/api/apontamento/snapshot?obra_id=1")[0], 401)
        cookie, csrf = self.login("usuario")
        self.assertEqual(self.request("POST", "/api/apontamento/sync?obra_id=1", {}, cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/apontamento/sync?obra_id=2", {}, cookie, csrf)[0], 403)
        self.assertEqual(self.request("GET", "/api/apontamento/snapshot?obra_id=2", cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/apontamento/health?obra_id=1", cookie=cookie)[0], 200)

    def test_payment_proposal_generation_edit_and_remove_require_authorized_session(self):
        cookie, csrf = self.login()
        with database.connect() as db:
            source = db.execute("SELECT * FROM lancamentos WHERE obra_id=1 AND tipo='produção' ORDER BY id LIMIT 1").fetchone()
        payload={"obra_id":1,"periodo_inicio":source["data"],"periodo_fim":source["data"],"pms_numero":source["pms_numero"]}
        status,_,proposal=self.request("POST","/api/payment-proposals/generate",payload,cookie,csrf)
        self.assertEqual(status,201)
        self.assertTrue(proposal["itens"])
        listed=self.request("GET",f"/api/payment-proposals?obra_id=1&start={source['data']}&end={source['data']}",cookie=cookie)
        self.assertEqual(listed[0],200)
        self.assertTrue(any(row["id"]==proposal["id"] for row in listed[2]))
        original=proposal["itens"][0]
        updated=self.request("PUT",f"/api/payment-proposals/{proposal['id']}/items/{original['id']}",
            {"observacao":"Conferido"},cookie,csrf)
        self.assertEqual(updated[0],200)
        self.assertEqual(next(row for row in updated[2]["itens"] if row["id"]==original["id"])["observacao"],"Conferido")
        removed=self.request("DELETE",f"/api/payment-proposals/{proposal['id']}/items/{original['id']}",cookie=cookie,csrf=csrf)
        self.assertEqual(removed[0],200)
        self.assertNotIn(original["id"],[row["id"] for row in removed[2]["itens"]])
        operator_cookie,_=self.login("usuario")
        self.assertEqual(self.request("GET","/api/payment-proposals?obra_id=1",cookie=operator_cookie)[0],403)

    def test_payment_proposal_exports_match_selected_snapshot_and_work_permissions(self):
        cookie, csrf = self.login()
        with database.connect() as db:
            source = db.execute("SELECT data,pms_numero FROM lancamentos WHERE obra_id=1 AND tipo='produção' AND ativo=1 ORDER BY id LIMIT 1").fetchone()
        status, _, proposal = self.request("POST", "/api/payment-proposals/generate",
            {"obra_id": 1, "periodo_inicio": source["data"], "periodo_fim": source["data"], "pms_numero": source["pms_numero"]}, cookie, csrf)
        self.assertEqual(status, 201)
        proposal_id = proposal["id"]
        pdf_status, _, pdf = self.request("GET", f"/api/payment-proposals/{proposal_id}.pdf", cookie=cookie)
        self.assertEqual(pdf_status, 200)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"RESUMO POR FUNCION", pdf)
        xlsx_status, _, workbook = self.request("GET", f"/api/payment-proposals/{proposal_id}.xlsx", cookie=cookie)
        self.assertEqual(xlsx_status, 200)
        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("RESUMO POR FUNCIONÁRIO", sheet)
            self.assertIn("TOTAL DA PROPOSTA", sheet)
            self.assertIn(str(proposal["totais"]["valor_pagar"]), sheet)
        self.assertEqual(self.request("GET", f"/api/payment-proposals/{proposal_id}.html", cookie=cookie)[0], 200)
        operator_cookie, _ = self.login("usuario")
        self.assertEqual(self.request("GET", f"/api/payment-proposals/{proposal_id}.pdf", cookie=operator_cookie)[0], 403)

    def test_closing_excel_export_uses_its_registered_period(self):
        cookie, _ = self.login()
        with database.connect() as db:
            source = db.execute("SELECT obra_id,data,pms_numero FROM lancamentos WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 1").fetchone()
            result = database.generate_closing(db,source["obra_id"],source["data"],source["data"],source["pms_numero"],user_id=1)
            closing = db.execute("SELECT id,obra_id,periodo_inicio,periodo_fim FROM fechamentos_pagamento WHERE id=?",(result["id"],)).fetchone()
        status, _, workbook = self.request("GET", f"/api/reports/pagamento.xlsx?obra_id={closing['obra_id']}&fechamento_id={closing['id']}&start=2020-01-01&end=2020-01-15", cookie=cookie)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn(f"{closing['periodo_inicio']} a {closing['periodo_fim']}", sheet)
            self.assertIn(f"FECHAMENTO FINANCEIRO #{closing['id']}", sheet)
        self.assertEqual(self.request("GET", f"/api/reports/pagamento.pdf?obra_id=2&fechamento_id={closing['id']}", cookie=cookie)[0], 400)

    def test_entry_delete_is_admin_only_soft_and_requires_reason(self):
        cookie, csrf = self.login()
        with database.connect() as db:
            source = db.execute("SELECT * FROM lancamentos WHERE obra_id=1 AND pms_numero IS NULL AND ativo=1 ORDER BY id LIMIT 1").fetchone()
            self.assertIsNotNone(source)
            created = database.create_entry(db, {
                "obra_id": source["obra_id"], "data": source["data"],
                "funcionario_id": source["funcionario_id"], "servico_id": source["servico_id"],
                "tipo": source["tipo"], "quantidade_m2": source["quantidade_m2"],
                "horas_trabalhadas": source["horas_trabalhadas"], "extra": source["extra"],
                "desconto": source["desconto"], "bloco": source["bloco"], "ap": source["ap"],
                "casa": source["casa"], "obs": "Fixture de exclusão lógica",
            }, user_id=1)
        entry_id = created["id"]
        self.assertEqual(self.request("DELETE",f"/api/entries/{entry_id}",{},cookie,csrf)[0],400)
        self.assertEqual(self.request("DELETE",f"/api/entries/{entry_id}",{"motivo":"Duplicado"},cookie,csrf)[0],200)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT ativo FROM lancamentos WHERE id=?",(entry_id,)).fetchone()[0],0)
        operator_cookie, operator_csrf = self.login("usuario")
        self.assertEqual(self.request("DELETE",f"/api/entries/{entry_id}",{"motivo":"Sem autorização"},operator_cookie,operator_csrf)[0],403)

    def test_record_persists_after_new_connection_and_tombstone(self):
        cookie, csrf = self.login()
        row = {"id": "http-worker", "name": "Teste HTTP", "active": True, "updatedAt": "2026-09-25T12:00:00.000Z"}
        status, _, body = self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [row]}, cookie, csrf)
        self.assertEqual(status, 200)
        self.assertEqual(body["processed"], 1)
        repeat = self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [row]}, cookie, csrf)
        self.assertEqual(repeat[2]["processed"], 0)
        with database.connect() as db:
            persisted = json.loads(db.execute("SELECT data FROM apontamento_records WHERE obra_id=1 AND id='http-worker'").fetchone()[0])
            self.assertEqual(persisted, {**row, "name": "TESTE HTTP"})
        row.update(_deleted=True, updatedAt="2026-09-25T12:01:00.000Z")
        self.assertEqual(self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [row]}, cookie, csrf)[0], 200)
        rows = self.request("GET", "/api/apontamento/snapshot?obra_id=1", cookie=cookie)[2]["employees"]
        self.assertTrue(next(r for r in rows if r["id"] == row["id"])["_deleted"])

    def test_invalid_batch_rolls_back(self):
        cookie, csrf = self.login()
        employee = {"id": "rollback-worker", "name": "Não persistir", "updatedAt": "2026-09-25T12:00:00Z"}
        appointment = {"id": "rollback-appointment", "date": "2026-09-25", "employeeIds": [employee["id"]], "locationIds": ["missing"], "serviceId": "missing", "quantity": 1, "status": "em_andamento", "updatedAt": employee["updatedAt"]}
        status, _, _ = self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [employee], "appointments": [appointment]}, cookie, csrf)
        self.assertEqual(status, 400)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM apontamento_records WHERE id LIKE 'rollback-%'").fetchone()[0], 0)

    def test_validation_rejects_bad_json_dates_and_injected_ids(self):
        cookie, csrf = self.login()
        for payload in ([], {"employees": [{"id": '<img onerror="x">', "updatedAt": "2026-09-25T12:00:00Z", "name": "x"}]}, {"employees": [{"id": "bad-time", "updatedAt": "tomorrow", "name": "x"}]}, {"appointments": [{"id": "bad-point", "updatedAt": "2026-09-25T12:00:00Z"}]}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("POST", "/api/apontamento/sync?obra_id=1", payload, cookie, csrf)[0], 400)

    def test_timestamp_comparison_uses_timezone(self):
        cookie, csrf = self.login()
        row = {"id": "timezone-worker", "name": "Mais novo", "updatedAt": "2026-09-25T12:00:00-03:00"}
        self.assertEqual(self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [row]}, cookie, csrf)[0], 200)
        older = {**row, "name": "Antigo", "updatedAt": "2026-09-25T14:00:00Z"}
        body = self.request("POST", "/api/apontamento/sync?obra_id=1", {"employees": [older]}, cookie, csrf)[2]
        self.assertEqual(next(r for r in body["snapshot"]["employees"] if r["id"] == row["id"])["name"], "MAIS NOVO")

    def test_all_existing_read_views_and_pagination(self):
        cookie, _ = self.login()
        for endpoint in ("bootstrap", "works", "entries", "appropriations", "quality", "payments", "pms", "forecast", "summary", "reimbursements", "employees", "services", "users", "audit"):
            with self.subTest(endpoint=endpoint):
                status, _, body = self.request("GET", f"/api/{endpoint}?obra_id=1", cookie=cookie)
                self.assertEqual(status, 200, str(body)[:150])
        first = self.request("GET", "/api/entries?obra_id=1&page=1", cookie=cookie)[2]
        second = self.request("GET", "/api/entries?obra_id=1&page=2", cookie=cookie)[2]
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])

    def test_employee_service_price_routes_require_authorization_and_persist(self):
        admin_cookie,admin_csrf=self.login()
        with database.connect() as db:
            employee_id=db.execute("SELECT id FROM funcionarios WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 1").fetchone()["id"]
            service_id=db.execute("SELECT id FROM servicos WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 1").fetchone()["id"]
        payload={"obra_id":1,"funcionario_id":employee_id,"servico_id":service_id,
                 "preco_pagamento":123.45,"motivo":"Preço individual via HTTP"}
        status,_,created=self.request("POST","/api/employee-service-prices",payload,admin_cookie,admin_csrf)
        self.assertEqual(status,201,created)
        self.assertEqual(created["preco"]["preco_pagamento"],123.45)
        self.assertEqual(created["impacto"]["lancamentos_bloqueados"],0)
        status,_,rows=self.request("GET",f"/api/employee-service-prices?obra_id=1&servico_id={service_id}",cookie=admin_cookie)
        self.assertEqual(status,200)
        self.assertTrue(any(row["funcionario_id"]==employee_id and row["preco_pagamento"]==123.45 for row in rows))

        operator_cookie,operator_csrf=self.login("usuario")
        self.assertEqual(self.request("GET",f"/api/employee-service-prices?obra_id=1&servico_id={service_id}",cookie=operator_cookie)[0],403)
        self.assertEqual(self.request("POST","/api/employee-service-prices",payload,operator_cookie,operator_csrf)[0],403)

    def test_proposal_api_generates_edits_orders_and_exports_in_authorized_work(self):
        cookie, csrf = self.login()
        with database.connect() as db:
            day=db.execute("SELECT data FROM lancamentos WHERE obra_id=1 AND tipo='produção' ORDER BY id LIMIT 1").fetchone()["data"]
        status,_,proposal=self.request("POST","/api/proposals/generate",{
            "obra_id":1,"periodo_inicio":day,"periodo_fim":day,"data_proposta":day,"pms_numero":12
        },cookie,csrf)
        self.assertEqual(status,201)
        self.assertTrue(proposal["servicos"])
        service=proposal["servicos"][0]
        item=service["itens"][0]
        block_items=[row["id"] for row in service["itens"] if (row["bloco"] or "")== (item["bloco"] or "")]
        status,_,updated=self.request("PUT",f"/api/proposals/{proposal['id']}/blocks",{
            "item_ids":block_items,"bloco":"B-HTTP"
        },cookie,csrf)
        self.assertEqual(status,200)
        self.assertTrue(all(row["bloco"]=="B-HTTP" for row in updated["servicos"][0]["itens"] if row["id"] in block_items))
        status,_,updated=self.request("PUT",f"/api/proposals/{proposal['id']}/items/{item['id']}",{
            "bloco":"B-HTTP","casa":"QA-HTTP"
        },cookie,csrf)
        self.assertEqual(status,200)
        self.assertTrue(any(x["bloco"]=="B-HTTP" and x["casa"]=="QA-HTTP" for s in updated["servicos"] for x in s["itens"]))
        status,_,updated=self.request("POST",f"/api/proposals/{proposal['id']}/services",{"nome_servico":"Serviço manual"},cookie,csrf)
        self.assertEqual(status,201)
        service_ids=[s["id"] for s in reversed(updated["servicos"])]
        status,_,ordered=self.request("POST",f"/api/proposals/{proposal['id']}/reorder",{"servicos":service_ids},cookie,csrf)
        self.assertEqual(status,200)
        self.assertEqual([s["id"] for s in ordered["servicos"]],service_ids)
        status,headers,xlsx=self.request("GET",f"/api/proposals/{proposal['id']}.xlsx",cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(headers["Content-Type"],"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(xlsx.startswith(b"PK"))
        self.assertEqual(self.request("GET",f"/api/proposals/{proposal['id']}")[0],401)
        operator_cookie,operator_csrf=self.login("usuario")
        self.assertEqual(self.request("POST",f"/api/proposals/{proposal['id']}/services",{"nome_servico":"Negado"},operator_cookie,operator_csrf)[0],403)

    def test_quality_filters_and_paginates_without_losing_totals(self):
        cookie, _ = self.login()
        first = self.request("GET", "/api/quality?obra_id=1&situacao=nao_avaliado&page=1&limit=25", cookie=cookie)[2]
        second = self.request("GET", "/api/quality?obra_id=1&situacao=nao_avaliado&page=2&limit=25", cookie=cookie)[2]
        self.assertEqual(first["limit"], 25)
        self.assertGreater(first["total"], 25)
        self.assertEqual(first["pages"], (first["total"] + 24) // 25)
        self.assertEqual(len(first["items"]), 25)
        self.assertNotEqual(first["items"][0]["lancamento_id"], second["items"][0]["lancamento_id"])
        self.assertEqual(first["items"][0]["situacao"], "nao_avaliado")

    def test_settings_routes_persist_preferences_and_enforce_admin_permissions(self):
        admin_cookie, admin_csrf = self.login()
        status, _, data = self.request("GET", "/api/settings?obra_id=1", cookie=admin_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["work"]["politica_coletivo"], "dividir_igualmente")
        self.assertIn("sessions", data)

        user_cookie, user_csrf = self.login("usuario")
        self.assertEqual(self.request("PUT", "/api/settings/company", {"nome":"Sem permissão"}, user_cookie, user_csrf)[0], 403)
        self.assertEqual(self.request("PUT", "/api/settings/work?obra_id=1", {"responsavel_apontamento":"X", "bloquear_datas_futuras":True}, user_cookie, user_csrf)[0], 403)
        status, _, preference = self.request("PUT", "/api/settings/preferences", {
            "obra_padrao_id":1,"tela_inicial":"entries","linhas_por_pagina":50
        }, user_cookie, user_csrf)
        self.assertEqual(status, 200)
        self.assertEqual(preference["linhas_por_pagina"], 50)

        status, _, company = self.request("PUT", "/api/settings/company", {
            "nome":"Construtora de Teste","cnpj":"12.345.678/0001-90","email":"pms@example.com"
        }, admin_cookie, admin_csrf)
        self.assertEqual(status, 200)
        self.assertEqual(company["nome"], "CONSTRUTORA DE TESTE")
        status, _, work = self.request("PUT", "/api/settings/work?obra_id=1", {
            "responsavel_apontamento":"Responsável QA","bloquear_datas_futuras":True,
            "politica_coletivo":"dividir_igualmente"
        }, admin_cookie, admin_csrf)
        self.assertEqual(status, 200)
        self.assertEqual(work["politica_coletivo"], "dividir_igualmente")
        status, _, audit = self.request("GET", "/api/settings/audit?page=1&limit=10&q=Construtora", cookie=admin_cookie)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(audit["total"], 1)
        backup_status, backup_headers, backup_body = self.request("GET", "/api/settings/backup", cookie=admin_cookie)
        self.assertEqual(backup_status, 200)
        self.assertEqual(backup_headers["Content-Type"], "application/vnd.sqlite3")
        self.assertTrue(backup_body.startswith(b"SQLite format 3"))

    def test_offline_settings_cannot_override_server_rules_and_future_entries_are_blocked(self):
        cookie, csrf = self.login()
        self.request("PUT", "/api/settings/company", {"nome":"Nome oficial"}, cookie, csrf)
        status, _, result = self.request("POST", "/api/apontamento/sync?obra_id=1", {
            "settings":[{"id":"app","companyName":"Nome adulterado","blockFutureDates":False,
                         "updatedAt":"2026-09-25T12:00:00Z"}]
        }, cookie, csrf)
        self.assertEqual(status, 200)
        self.assertEqual(result["snapshot"]["settings"][0]["companyName"], "NOME OFICIAL")

        with database.connect() as db:
            employee = db.execute("SELECT id FROM funcionarios WHERE obra_id=1 AND ativo=1 LIMIT 1").fetchone()["id"]
            service = db.execute("SELECT id FROM servicos WHERE obra_id=1 AND ativo=1 LIMIT 1").fetchone()["id"]
        status, _, error = self.request("POST", "/api/entries", {
            "obra_id":1,"data":"9999-12-31","funcionario_id":employee,"servico_id":service,"tipo":"produção"
        }, cookie, csrf)
        self.assertEqual(status, 400)
        self.assertIn("datas futuras", error["error"])


if __name__ == "__main__":
    unittest.main()
