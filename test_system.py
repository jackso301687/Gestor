import tempfile
import unittest
import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

import database
import settings_store
from apontamento_store import merge_payload, snapshot, validate_payload
from reports import pdf_bytes, report_data, xlsx_bytes
import proposta_store
import proposta_export
from server import validate_period


class BusinessRulesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.patch = patch.object(database, "DB_PATH", self.db_path)
        self.patch.start()
        database.init_db()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_real_data_was_imported(self):
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM obras").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0], 6504)
            employees = dict(db.execute(
                "SELECT o.nome,COUNT(f.id) total FROM obras o LEFT JOIN funcionarios f ON f.obra_id=o.id GROUP BY o.id"
            ))
            services = dict(db.execute(
                "SELECT o.nome,COUNT(s.id) total FROM obras o LEFT JOIN servicos s ON s.obra_id=o.id GROUP BY o.id"
            ))
            self.assertEqual(employees["ATLANTA"], 49)
            self.assertEqual(employees["ATLANTA 2"], 43)
            self.assertEqual(services["ATLANTA"], 129)
            self.assertEqual(services["ATLANTA 2"], 129)

    def test_fresh_database_uses_backup_reference_when_primary_is_destination(self):
        with patch.object(database, "REFERENCE_DB", self.db_path):
            database.init_db()
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM obras").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0], 6504)

    def test_migrations_are_versioned_and_idempotent(self):
        with database.connect() as db:
            first = db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall()
            database.migrate_schema(db)
            second = db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall()
            columns = {row["name"] for row in db.execute("PRAGMA table_info(lancamentos)")}

        expected = [
            ("0001", "operational_columns"),
            ("0002", "editing_and_price_history"),
            ("0003", "apontamento_pwa_records"),
            ("0004", "application_settings_and_field_links"),
            ("0005", "propostas_medicao"),
            ("0006", "propostas_pagamento"),
            ("0007", "soft_delete_lancamentos"),
            ("0008", "employee_service_prices"),
            ("0009", "uppercase_current_names"),
        ]
        self.assertEqual([tuple(row) for row in first], expected)
        self.assertEqual([tuple(row) for row in second], expected)
        self.assertIn("pms_numero", columns)
        self.assertIn("atualizado_em", columns)
        self.assertIn("apontamento_origem_id", columns)
        self.assertIn("funcionario_origem_id", columns)
        self.assertEqual(db.execute("SELECT politica_coletivo FROM configuracoes_obra WHERE obra_id=1").fetchone()[0], "dividir_igualmente")

    def test_name_normalization_changes_current_catalogs_not_audit_snapshots(self):
        with database.connect() as db:
            employee = db.execute("SELECT id FROM funcionarios WHERE obra_id=1 LIMIT 1").fetchone()["id"]
            db.execute("UPDATE funcionarios SET nome='joão da silva' WHERE id=?", (employee,))
            db.execute("INSERT INTO apontamento_records(obra_id,kind,id,updated_at,data) VALUES(1,'employees','lowercase-test','2026-09-20T12:00:00Z',?)", ('{"id":"lowercase-test","name":"maria souza","updatedAt":"2026-09-20T12:00:00Z"}',))
            db.execute("INSERT INTO auditoria(entidade,acao,detalhes) VALUES('teste','historico','nome antigo: Maria Souza')")
            historical_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            changes = database.normalize_current_names(db)
            self.assertEqual(db.execute("SELECT nome FROM funcionarios WHERE id=?", (employee,)).fetchone()[0], "JOÃO DA SILVA")
            self.assertEqual(db.execute("SELECT data FROM apontamento_records WHERE id='lowercase-test'").fetchone()[0], '{"id":"lowercase-test","name":"MARIA SOUZA","updatedAt":"2026-09-20T12:00:00Z"}')
            self.assertEqual(db.execute("SELECT detalhes FROM auditoria WHERE id=?", (historical_id,)).fetchone()[0], "nome antigo: Maria Souza")
            self.assertIn("funcionarios", changes)

    def test_payment_proposal_grid_is_persisted_and_does_not_change_production(self):
        import proposta_pagamento_store

        with database.connect() as db:
            source = db.execute("SELECT * FROM lancamentos WHERE tipo='produção' ORDER BY id LIMIT 1").fetchone()
            original_pay = source["valor_pagar"]
            proposal = proposta_pagamento_store.create_from_entries(
                db, source["obra_id"], source["data"], source["data"], source["pms_numero"], 1, "127.0.0.1"
            )
            self.assertTrue(proposal["itens"])
            self.assertAlmostEqual(proposal["totais"]["valor_pagar"], sum(row["valor_pagar"] for row in proposal["itens"]))
            item = proposta_pagamento_store.add_item(db, proposal["id"], {
                "funcionario_id": source["funcionario_id"], "servico_id": source["servico_id"],
                "bloco": "B2", "ap": "204", "casa": "18", "quantidade": 2,
                "valor_gerado": 120, "valor_pagar": 95, "observacao": "Conferir acabamento",
            }, 1, "127.0.0.1")
            manual = next(row for row in item["itens"] if row["observacao"] == "Conferir acabamento")
            updated = proposta_pagamento_store.update_item(db,proposal["id"],manual["id"],{"valor_pagar":80,"observacao":"Revisado"},1,"127.0.0.1")
            self.assertEqual(next(row for row in updated["itens"] if row["id"]==manual["id"])["valor_pagar"],80)
            removed = proposta_pagamento_store.remove_item(db,proposal["id"],manual["id"],1,"127.0.0.1")
            self.assertNotIn(manual["id"],[row["id"] for row in removed["itens"]])
            self.assertEqual(db.execute("SELECT valor_pagar FROM lancamentos WHERE id=?",(source["id"],)).fetchone()[0],original_pay)

    def test_apontamento_database_is_imported_and_scoped_to_atlanta(self):
        with database.connect() as db:
            counts = {
                row["kind"]: row["total"]
                for row in db.execute(
                    """SELECT kind,COUNT(*) total FROM apontamento_records
                       WHERE obra_id=1 GROUP BY kind"""
                )
            }
            other_work = db.execute(
                "SELECT COUNT(*) FROM apontamento_records WHERE obra_id=2"
            ).fetchone()[0]
        self.assertEqual(counts["employees"], 122)
        self.assertEqual(counts["services"], 322)
        self.assertEqual(counts["locations"], 281)
        self.assertEqual(counts["appointments"], 2)
        self.assertEqual(other_work, 0)

    def test_apontamento_sync_preserves_newest_record_and_work_isolation(self):
        newer = {
            "employees": [{
                "id": "worker-test",
                "name": "Registro novo",
                "active": True,
                "updatedAt": "2026-09-23T12:00:00Z",
            }]
        }
        older = {
            "employees": [{
                "id": "worker-test",
                "name": "Registro antigo",
                "active": True,
                "updatedAt": "2026-09-22T12:00:00Z",
            }]
        }
        with database.connect() as db:
            merge_payload(db, 1, newer, user_id=1, ip="127.0.0.1")
            merge_payload(db, 1, older, user_id=1, ip="127.0.0.1")
            merge_payload(db, 2, {"employees": [{**newer["employees"][0], "name": "Outra obra"}]}, user_id=1)
            first = next(row for row in snapshot(db, 1)["employees"] if row["id"] == "worker-test")
            second = next(row for row in snapshot(db, 2)["employees"] if row["id"] == "worker-test")
        self.assertEqual(first["name"], "REGISTRO NOVO")
        self.assertEqual(second["name"], "OUTRA OBRA")

    def test_apontamento_sync_rejects_unknown_store(self):
        with self.assertRaisesRegex(ValueError, "desconhecida"):
            validate_payload({"payments": []})

    def test_collective_field_appointment_imports_equal_shares_once_as_production(self):
        with database.connect() as db:
            employees = db.execute(
                "SELECT id,nome FROM funcionarios WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 3"
            ).fetchall()
            service = db.execute(
                "SELECT id,nome_interno FROM servicos WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 1"
            ).fetchone()
            field_employees = [{"id": f"collective-worker-{index}", "name": row["nome"], "active": True,
                                "updatedAt": "2026-09-25T12:00:00Z"} for index, row in enumerate(employees)]
            payload = {
                "employees": field_employees,
                "services": [{"id":"collective-service", "name":service["nome_interno"], "active":True,
                              "updatedAt":"2026-09-25T12:00:00Z"}],
                "locations": [{"id":"collective-location", "label":"Casa 12 Bloco B AP 3", "type":"unidade",
                               "updatedAt":"2026-09-25T12:00:00Z"}],
                "appointments": [{"id":"collective-appointment", "date":"2026-09-25",
                                   "employeeIds":[row["id"] for row in field_employees],
                                   "locationIds":["collective-location"], "serviceId":"collective-service",
                                   "quantity":1, "unit":"un.", "status":"finalizado",
                                   "observation":"Teste coletivo", "updatedAt":"2026-09-25T12:05:00Z"}],
            }
            merge_payload(db, 1, payload, user_id=1)
            before_measurements = db.execute("SELECT COUNT(*) FROM medicoes_pms WHERE obra_id=1").fetchone()[0]
            before_payments = db.execute("SELECT COUNT(*) FROM fechamentos_pagamento WHERE obra_id=1").fetchone()[0]
            result = settings_store.import_field_appointment(db, 1, "collective-appointment", 1)
            shares = db.execute(
                "SELECT quantidade_m2,apontamento_origem_id,funcionario_origem_id FROM lancamentos "
                "WHERE obra_id=1 AND apontamento_origem_id='collective-appointment' ORDER BY id"
            ).fetchall()
            after_measurements = db.execute("SELECT COUNT(*) FROM medicoes_pms WHERE obra_id=1").fetchone()[0]
            after_payments = db.execute("SELECT COUNT(*) FROM fechamentos_pagamento WHERE obra_id=1").fetchone()[0]

            self.assertEqual(len(result["lancamentos"]), 3)
            self.assertEqual([round(row["quantidade_m2"], 4) for row in shares], [0.3333, 0.3333, 0.3334])
            self.assertEqual(sum(row["quantidade_m2"] for row in shares), 1)
            self.assertTrue(all(row["apontamento_origem_id"] == "collective-appointment" for row in shares))
            self.assertEqual(len({row["funcionario_origem_id"] for row in shares}), 3)
            self.assertEqual(after_measurements, before_measurements)
            self.assertEqual(after_payments, before_payments)
            with self.assertRaisesRegex(ValueError, "já foi importado"):
                settings_store.import_field_appointment(db, 1, "collective-appointment", 1)

    def test_database_backup_restore_is_validated_and_keeps_a_pre_restore_copy(self):
        with database.connect() as db:
            settings_store.update_company(db, {"nome":"Empresa antes"}, 1)
            original = settings_store.backup_database_bytes(db)
            db.execute("UPDATE configuracoes_empresa SET nome='Empresa depois' WHERE id=1")
            db.commit()
            with self.assertRaisesRegex(ValueError, "Digite RESTAURAR"):
                settings_store.restore_database(db, original, "", 1)
            result = settings_store.restore_database(db, original, "RESTAURAR", 1)
            self.assertTrue(result["ok"])
            self.assertEqual(db.execute("SELECT nome FROM configuracoes_empresa WHERE id=1").fetchone()[0], "EMPRESA ANTES")
            backup = self.db_path.parent / "backups" / result["backup_automatico"]
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_existing_operational_user_receives_explicit_work_access(self):
        with database.connect() as db:
            user = db.execute("SELECT * FROM usuarios WHERE username='usuario'").fetchone()
            works = [row["id"] for row in db.execute("SELECT id FROM obras ORDER BY id")]
            assigned = [
                row["obra_id"]
                for row in db.execute(
                    "SELECT obra_id FROM usuario_obras WHERE usuario_id=? ORDER BY obra_id",
                    (user["id"],),
                )
            ]
            self.assertEqual(assigned, works)
            self.assertTrue(database.has_work_access(db, user, works[0]))

            db.execute("DELETE FROM usuario_obras WHERE usuario_id=?", (user["id"],))
            db.execute(
                "INSERT INTO usuario_obras(usuario_id,obra_id) VALUES(?,?)",
                (user["id"], works[0]),
            )
            self.assertTrue(database.has_work_access(db, user, works[0]))
            self.assertFalse(database.has_work_access(db, user, works[1]))

    def test_appropriation_rejects_relationships_from_another_work(self):
        with database.connect() as db:
            employee = db.execute("SELECT id FROM funcionarios WHERE obra_id=1 LIMIT 1").fetchone()
            other_work = db.execute("SELECT id FROM obras WHERE id<>1 LIMIT 1").fetchone()
            cursor = db.execute(
                "INSERT INTO servicos(obra_id,nome_interno,preco_pagamento,ativo) VALUES(?,?,?,1)",
                (other_work["id"], "Serviço isolado de teste", 1),
            )
            with self.assertRaisesRegex(ValueError, "não pertence à obra"):
                database.create_appropriation(db, {
                    "obra_id": 1,
                    "data": "2026-09-21",
                    "funcionario_id": employee["id"],
                    "servico_id": cursor.lastrowid,
                })

    def test_entry_update_rejects_relationships_from_another_work(self):
        with database.connect() as db:
            entry = db.execute(
                "SELECT id FROM lancamentos WHERE obra_id=1 AND ativo=1 LIMIT 1"
            ).fetchone()
            other_work = db.execute("SELECT id FROM obras WHERE id<>1 LIMIT 1").fetchone()
            cursor = db.execute(
                "INSERT INTO servicos(obra_id,nome_interno,preco_pagamento,ativo) VALUES(?,?,?,1)",
                (other_work["id"], "Serviço cruzado de teste", 1),
            )
            with self.assertRaisesRegex(ValueError, "não pertence à obra"):
                database.update_entry(db, entry["id"], {"servico_id": cursor.lastrowid})

    def test_service_price_history_applies_price_by_date(self):
        with database.connect() as db:
            cursor = db.execute(
                """INSERT INTO servicos
                   (obra_id,nome_interno,preco_pagamento,valor_receber_unitario,quantidade_padrao,ativo)
                   VALUES(?,?,?,?,?,1)""",
                (1, "Cerâmica histórica de teste", 30, 48, 1),
            )
            service_id = cursor.lastrowid
            database.ensure_service_price_history(db)
            created = database.create_service_price(db, {
                "servico_id": service_id,
                "obra_id": 1,
                "preco_pagamento": 35,
                "valor_receber_unitario": 55,
                "inicio_vigencia": "2026-10-01",
                "motivo": "Novo preço contratual",
            }, user_id=1, ip="127.0.0.1")

            september = database.service_price_at(db, service_id, "2026-09-15")
            october = database.service_price_at(db, service_id, "2026-10-15")
            history = db.execute(
                "SELECT * FROM servico_precos WHERE servico_id=? ORDER BY inicio_vigencia",
                (service_id,),
            ).fetchall()

            self.assertEqual(september["preco_pagamento"], 30)
            self.assertEqual(october["preco_pagamento"], 35)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["fim_vigencia"], "2026-09-30")
            self.assertEqual(created["inicio_vigencia"], "2026-10-01")

    def test_employee_service_price_updates_open_fortnight_and_remains_effective(self):
        with database.connect() as db:
            employees=db.execute("SELECT id FROM funcionarios WHERE obra_id=1 AND ativo=1 ORDER BY id LIMIT 2").fetchall()
            service_id=db.execute(
                "INSERT INTO servicos(obra_id,nome_interno,preco_pagamento,valor_receber_unitario,quantidade_padrao,ativo) VALUES(1,?,?,?,?,1)",
                ("Preço individual QA",100,25,1),
            ).lastrowid
            employee_id,other_employee_id=(row["id"] for row in employees)
            common={"obra_id":1,"funcionario_id":employee_id,"servico_id":service_id,"tipo":"produção","quantidade_m2":2}
            opened=database.create_entry(db,{**common,"data":"2026-09-16"})
            locked=database.create_entry(db,{**common,"data":"2026-09-17","pms_numero":77})
            before_fortnight=database.create_entry(db,{**common,"data":"2026-09-15"})
            other=database.create_entry(db,{**common,"funcionario_id":other_employee_id,"data":"2026-09-16"})

            result=database.create_employee_service_price(db,{
                "obra_id":1,"funcionario_id":employee_id,"servico_id":service_id,
                "preco_pagamento":120,"motivo":"Valor individual combinado",
            },user_id=1,today=date(2026,9,20))
            current=db.execute("SELECT * FROM lancamentos WHERE id=?",(opened["id"],)).fetchone()
            still_locked=db.execute("SELECT * FROM lancamentos WHERE id=?",(locked["id"],)).fetchone()
            old_period=db.execute("SELECT * FROM lancamentos WHERE id=?",(before_fortnight["id"],)).fetchone()
            other_entry=db.execute("SELECT * FROM lancamentos WHERE id=?",(other["id"],)).fetchone()
            future_service=database.service_with_effective_price(
                db,db.execute("SELECT * FROM servicos WHERE id=?",(service_id,)).fetchone(),"2026-10-01",employee_id
            )
            future_entry=database.create_entry(db,{**common,"data":"2026-10-01"})

        self.assertEqual(result["periodo"],{"inicio":"2026-09-16","fim":"2026-09-30"})
        self.assertEqual(result["impacto"],{"lancamentos_atualizados":1,"lancamentos_bloqueados":1})
        self.assertEqual(current["valor_pagar"],240)
        self.assertEqual(current["valor_receber"],50)
        self.assertEqual(still_locked["valor_pagar"],200)
        self.assertEqual(old_period["valor_pagar"],200)
        self.assertEqual(other_entry["valor_pagar"],200)
        self.assertEqual(future_service["preco_pagamento"],120)
        self.assertEqual(future_entry["valor_pagar"],240)

    def test_price_correction_requires_reason_and_is_audited(self):
        with database.connect() as db:
            price = db.execute("SELECT * FROM servico_precos ORDER BY id LIMIT 1").fetchone()
            with self.assertRaisesRegex(ValueError, "motivo"):
                database.correct_service_price(db, price["id"], {"preco_pagamento": 99}, user_id=1)

            result = database.correct_service_price(db, price["id"], {
                "preco_pagamento": float(price["preco_pagamento"]) + 1,
                "valor_receber_unitario": price["valor_receber_unitario"],
                "motivo_correcao": "Correção controlada de teste",
                "versao": price["versao"],
            }, user_id=1, ip="127.0.0.1")
            audit = db.execute(
                "SELECT * FROM auditoria WHERE entidade='servico_preco' AND entidade_id=? AND acao='corrigido'",
                (price["id"],),
            ).fetchone()

            self.assertEqual(result["preco"]["versao"], price["versao"] + 1)
            self.assertIsNotNone(audit)
            self.assertIn("Correção controlada de teste", audit["detalhes"])

    def test_entry_can_be_edited_with_persistence_and_audit(self):
        with database.connect() as db:
            entry = db.execute(
                """SELECT l.* FROM lancamentos l
                   WHERE l.pms_numero IS NULL
                     AND NOT EXISTS (SELECT 1 FROM itens_pagamento i WHERE i.lancamento_id=l.id)
                   LIMIT 1"""
            ).fetchone()
            updated = database.update_entry(
                db, entry["id"], {"casa": "219", "versao": entry["versao"]}, user_id=1, ip="127.0.0.1"
            )
            persisted = db.execute("SELECT * FROM lancamentos WHERE id=?", (entry["id"],)).fetchone()
            audit = db.execute(
                "SELECT * FROM auditoria WHERE entidade='lancamento' AND entidade_id=? AND acao='alterado' ORDER BY id DESC LIMIT 1",
                (entry["id"],),
            ).fetchone()

            self.assertEqual(updated["casa"], "219")
            self.assertEqual(persisted["casa"], "219")
            self.assertEqual(persisted["versao"], entry["versao"] + 1)
            self.assertIsNotNone(audit)

    def test_measured_entry_requires_controlled_retification(self):
        with database.connect() as db:
            entry = db.execute("SELECT * FROM lancamentos LIMIT 1").fetchone()
            db.execute("UPDATE lancamentos SET pms_numero=999 WHERE id=?", (entry["id"],))
            current = db.execute("SELECT * FROM lancamentos WHERE id=?", (entry["id"],)).fetchone()
            with self.assertRaises(PermissionError):
                database.update_entry(db, entry["id"], {"casa": "300", "versao": current["versao"]}, user_id=1)
            updated = database.update_entry(
                db, entry["id"],
                {"casa": "300", "versao": current["versao"], "motivo_alteracao": "Retificação administrativa"},
                user_id=1, allow_retification=True,
            )
            self.assertEqual(updated["casa"], "300")

    def test_entry_optimistic_lock_rejects_stale_version(self):
        with database.connect() as db:
            entry = db.execute(
                """SELECT l.* FROM lancamentos l
                   WHERE l.pms_numero IS NULL
                     AND NOT EXISTS (SELECT 1 FROM itens_pagamento i WHERE i.lancamento_id=l.id)
                   LIMIT 1"""
            ).fetchone()
            database.update_entry(db, entry["id"], {"casa": "218", "versao": entry["versao"]}, user_id=1)
            with self.assertRaisesRegex(ValueError, "outro usuário"):
                database.update_entry(db, entry["id"], {"casa": "219", "versao": entry["versao"]}, user_id=1)

    def test_quality_correction_preserves_both_results(self):
        with database.connect() as db:
            launch_id = db.execute("SELECT id FROM lancamentos LIMIT 1").fetchone()["id"]
            database.save_quality(db, launch_id, "pendente", "Reprovado no primeiro exame", user_id=1)
            database.save_quality(db, launch_id, "ok", "Aprovado apó correção", user_id=1)
            history = db.execute(
                "SELECT situacao FROM qualidade_historico WHERE lancamento_id=? ORDER BY id",
                (launch_id,),
            ).fetchall()
            current = db.execute(
                "SELECT situacao FROM avaliacoes_qualidade WHERE lancamento_id=?", (launch_id,)
            ).fetchone()
            self.assertEqual([row["situacao"] for row in history[-2:]], ["pendente", "ok"])
            self.assertEqual(current["situacao"], "ok")

    def test_quality_problem_requires_observation(self):
        with database.connect() as db:
            launch_id = db.execute("SELECT id FROM lancamentos LIMIT 1").fetchone()["id"]
            with self.assertRaisesRegex(ValueError, "Descreva o problema"):
                database.save_quality(db, launch_id, "pendente", "", user_id=1)
            self.assertIsNone(db.execute(
                "SELECT id FROM avaliacoes_qualidade WHERE lancamento_id=?", (launch_id,)
            ).fetchone())

    def test_quality_quick_ok_is_persisted_and_historic(self):
        with database.connect() as db:
            launch_id = db.execute("SELECT id FROM lancamentos LIMIT 1").fetchone()["id"]
            result = database.save_quality(
                db, launch_id, "ok", "Aprovado pelo botão rápido de qualidade.", user_id=1
            )
            history = db.execute(
                "SELECT * FROM qualidade_historico WHERE lancamento_id=? ORDER BY id DESC LIMIT 1",
                (launch_id,),
            ).fetchone()
            self.assertEqual(result["situacao"], "ok")
            self.assertEqual(history["situacao"], "ok")
            self.assertEqual(history["observacao"], "Aprovado pelo botão rápido de qualidade.")

    def test_price_permission_is_enforced_by_profile(self):
        with database.connect() as db:
            admin = db.execute("SELECT * FROM usuarios WHERE username='admin'").fetchone()
            operator = db.execute("SELECT * FROM usuarios WHERE username='usuario'").fetchone()
            self.assertTrue(database.has_permission(admin, "servico.preco.alterar"))
            self.assertFalse(database.has_permission(operator, "servico.preco.alterar"))

    def test_global_period_filters_dashboard_and_reports(self):
        with database.connect() as db:
            selected = db.execute(
                """SELECT obra_id,data,COUNT(*) total FROM lancamentos
                   GROUP BY obra_id,data HAVING COUNT(*)>0 ORDER BY total DESC LIMIT 1"""
            ).fetchone()
            filtered = database.dashboard(db, selected["obra_id"], selected["data"], selected["data"])
            report = report_data(db, "dados", {
                "obra_id": [str(selected["obra_id"])], "start": [selected["data"]], "end": [selected["data"]]
            })
            expected = db.execute(
                "SELECT COUNT(*) FROM lancamentos WHERE obra_id=? AND data=?",
                (selected["obra_id"], selected["data"]),
            ).fetchone()[0]

            self.assertEqual(filtered["total_lancamentos"], expected)
            self.assertEqual(report["totals"]["linhas"], expected)
            self.assertTrue(all(row["data"] == selected["data"] for row in report["rows"]))

    def test_global_period_rejects_invalid_or_inverted_dates(self):
        self.assertEqual(validate_period("2026-09-01", "2026-09-30"), ("2026-09-01", "2026-09-30"))
        with self.assertRaisesRegex(ValueError, "inicial"):
            validate_period("2026-10-01", "2026-09-30")
        with self.assertRaisesRegex(ValueError, "formato"):
            validate_period("01/09/2026", "2026-09-30")

    def test_pdf_export_works_without_optional_dependencies(self):
        with database.connect() as db:
            selected = db.execute("SELECT obra_id,data FROM lancamentos LIMIT 1").fetchone()
            report = report_data(db, "dados", {
                "obra_id": [str(selected["obra_id"])], "start": [selected["data"]], "end": [selected["data"]]
            })
            content = pdf_bytes(report)
            self.assertTrue(content.startswith(b"%PDF-"))
            self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_xlsx_export_works_without_optional_dependencies(self):
        import io
        import zipfile
        import xml.etree.ElementTree as ET

        with database.connect() as db:
            selected = db.execute("SELECT obra_id,data FROM lancamentos LIMIT 1").fetchone()
            report = report_data(db, "dados", {
                "obra_id": [str(selected["obra_id"])], "start": [selected["data"]], "end": [selected["data"]]
            })
            content = xlsx_bytes(report)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                required = {"[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml", "xl/worksheets/sheet1.xml"}
                self.assertTrue(required.issubset(archive.namelist()))
                for name in required:
                    ET.fromstring(archive.read(name))

    def test_proposal_groups_services_deduplicates_locations_and_exports_template(self):
        import io
        import zipfile
        with database.connect() as db:
            source = db.execute("SELECT * FROM lancamentos WHERE tipo='produção' AND casa IS NOT NULL AND TRIM(casa)<>'' LIMIT 1").fetchone()
            db.execute(
                """INSERT INTO lancamentos(obra_id,data,funcionario_id,tipo,servico_id,bloco,ap,casa,valor_pagar,valor_receber,obs)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (source["obra_id"],source["data"],source["funcionario_id"],source["tipo"],source["servico_id"],source["bloco"],source["ap"],source["casa"],0,0,"não importar"),
            )
            proposal = proposta_store.create_from_entries(db, source["obra_id"], source["data"], source["data"], source["data"], 99, 1, "127.0.0.1")
            for service in proposal["servicos"]:
                locations = [(item["bloco"], item["casa"]) for item in service["itens"]]
                self.assertEqual(len(locations), len(set(locations)))
            self.assertTrue(all("observ" not in item for service in proposal["servicos"] for item in service["itens"]))
            content = proposta_export.xlsx_bytes(proposal)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
            self.assertIn(b"ES Souza Constru", archive.read("xl/worksheets/sheet1.xml"))

    def test_proposal_grid_and_template_paginate_without_fixed_group_limit(self):
        import io
        import zipfile
        import xml.etree.ElementTree as ET
        with database.connect() as db:
            source = db.execute("SELECT * FROM lancamentos WHERE obra_id=1 AND tipo='produção' LIMIT 1").fetchone()
            for house in range(1,31):
                db.execute(
                    """INSERT INTO lancamentos(obra_id,data,funcionario_id,tipo,servico_id,bloco,ap,casa,valor_pagar,valor_receber,obs)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (source["obra_id"],source["data"],source["funcionario_id"],source["tipo"],source["servico_id"],"B1",None,f"QA-{house:02}",0,0,"não importar"),
                )
            proposal=proposta_store.create_from_entries(db,1,source["data"],source["data"],source["data"],1,1,"127.0.0.1")
            service=next(row for row in proposal["servicos"] if any(item["casa"].startswith("QA-") for item in row["itens"]))
            self.assertEqual(sum(item["casa"].startswith("QA-") for item in service["itens"]),30)
            content=proposta_export.xlsx_bytes(proposal)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            workbook=ET.fromstring(archive.read("xl/workbook.xml"))
            ns={
                "m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                "p":"http://schemas.openxmlformats.org/package/2006/relationships",
                "ct":"http://schemas.openxmlformats.org/package/2006/content-types",
            }
            sheets=workbook.find("m:sheets",ns)
            expected_pages=(len(proposta_export._lines(proposal))+proposta_export.GRID_CAPACITY-1)//proposta_export.GRID_CAPACITY
            self.assertEqual(len(sheets),1)
            self.assertEqual(sheets[0].get("name"),"Proposta")
            relations=ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets={row.get("Id"):row.get("Target") for row in relations.findall("p:Relationship",ns)}
            content_types=ET.fromstring(archive.read("[Content_Types].xml"))
            overrides={row.get("PartName") for row in content_types.findall("ct:Override",ns)}
            definitions=workbook.find("m:definedNames",ns)
            print_areas=definitions.findall("m:definedName[@name='_xlnm.Print_Area']",ns)
            try:
                shared=ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings=["".join(row.itertext()) for row in shared.findall("m:si",ns)]
            except KeyError:
                shared_strings=[]
            expected_grid_last=proposta_export.GRID_FIRST_ROW+expected_pages*proposta_export.GRID_CAPACITY-1
            expected_signature_1,expected_signature_2=expected_grid_last+4,expected_grid_last+8
            self.assertEqual(len(print_areas),1)
            self.assertTrue(print_areas[0].text.endswith(f"$N${expected_signature_2}"))
            print_titles=definitions.find("m:definedName[@name='_xlnm.Print_Titles']",ns)
            self.assertEqual(print_titles.text,"'Proposta'!$1:$5")
            qa_houses=[]
            for index,sheet_node in enumerate(sheets,1):
                relationship=sheet_node.get("{"+ns["r"]+"}id")
                target=targets[relationship]
                self.assertEqual(target,"worksheets/sheet1.xml")
                self.assertIn("/xl/worksheets/sheet1.xml",overrides)
                sheet=ET.fromstring(archive.read("xl/"+target))
                breaks=sheet.find("m:rowBreaks",ns)
                break_ids=[] if breaks is None else [int(node.get("id")) for node in breaks.findall("m:brk",ns)]
                self.assertEqual(break_ids,[proposta_export.GRID_FIRST_ROW+proposta_export.GRID_CAPACITY*page-1 for page in range(1,expected_pages)])
                cells={cell.get("r"):cell for cell in sheet.findall(".//m:c",ns)}

                def text_at(reference):
                    cell=cells.get(reference)
                    if cell is None:
                        return ""
                    inline=cell.find("m:is",ns)
                    if inline is not None:
                        return "".join(inline.itertext())
                    value=cell.find("m:v",ns)
                    if cell.get("t")=="s" and value is not None:
                        return shared_strings[int(value.text)]
                    return "" if value is None else value.text

                self.assertEqual(text_at("A3"),f"EMPRESA: {proposal['empresa']}")
                self.assertEqual(text_at("B3"),f"OBRA: {proposal['obra']}")
                self.assertTrue(text_at("J3").startswith("DATA "))
                self.assertEqual(text_at("A4"),"SERVIÇOS")
                self.assertEqual(text_at("B4"),"BLOCO")
                self.assertEqual(text_at("C4"),"CASA")
                self.assertEqual(text_at("D4"),"OBSERVAÇÕES")
                self.assertEqual(text_at("C5"),"")
                qa_houses.extend(text_at(f"C{row}") for row in range(proposta_export.GRID_FIRST_ROW,expected_grid_last+1) if text_at(f"C{row}").startswith("QA-"))
                self.assertIn(f"A{expected_signature_1}",cells)
                self.assertIn(f"D{expected_signature_2}",cells)
            self.assertEqual(sorted(qa_houses),[f"QA-{house:02}" for house in range(1,31)])

    def test_proposal_xlsx_keeps_all_print_pages_on_one_worksheet(self):
        import io
        import zipfile
        import xml.etree.ElementTree as ET
        proposal={"empresa":"Empresa teste","obra":"Obra teste","data_proposta":"2026-09-27",
                  "servicos":[{"nome_servico":"Serviço contínuo","itens":[
                      {"bloco":"A","casa":str(index)} for index in range(1,98)
                  ]}]}
        content=proposta_export.xlsx_bytes(proposal)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            workbook=ET.fromstring(archive.read("xl/workbook.xml"))
            sheet=workbook.find("m:sheets/m:sheet",{"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"})
            self.assertIsNotNone(sheet)
            self.assertEqual(sheet.get("name"),"Proposta")
            self.assertEqual(len(workbook.find("m:sheets",{"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"})),1)
            sheet_xml=ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            ns={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            self.assertEqual([int(node.get("id")) for node in sheet_xml.findall("m:rowBreaks/m:brk",ns)],[53,101])
            cells={cell.get("r"):cell for cell in sheet_xml.findall(".//m:c",ns)}
            for reference in ("A6","A54","A102"):
                inline=cells[reference].find("m:is",ns)
                self.assertEqual("".join(inline.itertext()),"Serviço contínuo")
            self.assertIn("A153",cells)
            self.assertIn("D157",cells)

    def test_proposal_pdf_grid_packs_48_rows_and_signs_only_last_page(self):
        proposal={"empresa":"Empresa teste","obra":"Obra teste","data_proposta":"2026-09-27",
                  "servicos":[{"nome_servico":"Serviço A","itens":[
                      {"bloco":"A","casa":str(index)} for index in range(1,proposta_export.GRID_CAPACITY+2)
                  ]}]}
        rows=proposta_export._lines(proposal)
        pages=proposta_export._pages(rows)
        self.assertEqual(len(pages),2)
        first=proposta_export._page_html(pages[0],proposal,1,2,"")
        last=proposta_export._page_html(pages[1],proposal,2,2,"")
        self.assertEqual(first.count("<tr>"),proposta_export.GRID_CAPACITY+1)
        self.assertNotIn("Ass.: Responsável pela Empresa",first)
        self.assertIn("Ass.: Responsável pela Empresa",last)

    def test_proposal_pdf_has_serverless_fallback_without_chrome(self):
        proposal={"empresa":"Empresa teste","obra":"Obra teste","data_proposta":"2026-09-27",
                  "servicos":[{"nome_servico":"Serviço A","itens":[{"bloco":"A","casa":"1"}]}]}
        with patch.object(proposta_export.subprocess, "run", side_effect=FileNotFoundError):
            content=proposta_export.pdf_bytes(proposal)
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_every_rendered_action_has_a_click_binding(self):
        source = (Path(__file__).parent / "app-system.js").read_text(encoding="utf-8")
        rendered_actions = set(re.findall(r'data-action="([a-z-]+)"', source))
        bound_actions = set(re.findall(r"querySelectorAll\('\[data-action=\"([a-z-]+)\"\]'\)", source))
        self.assertEqual(rendered_actions - bound_actions, set(), "Existem botões data-action sem handler")

        interactive_attributes = {
            "data-view", "data-page", "data-quality", "data-quality-history",
            "data-entry-edit", "data-employee-edit", "data-service-edit", "data-user-edit",
            "data-entry-delete",
            "data-price-history", "data-history-entity", "data-appropriation-edit",
            "data-reimbursement-edit", "data-forecast-edit", "data-deactivate",
            "data-user-reset", "data-quality-ok",
        }
        for attribute in interactive_attributes:
            self.assertIn(f"querySelectorAll('[{attribute}]')", source, f"{attribute} sem handler")

    def test_quantity_has_priority_and_generates_receivable(self):
        service = {"preco_pagamento": 19, "valor_receber_unitario": 37.84, "quantidade_padrao": 1}
        pay, receive = database.calculate_entry(service, 2, 8, 10, 5)
        self.assertEqual(pay, 43)
        self.assertEqual(receive, 75.68)

    def test_hours_do_not_generate_receivable(self):
        service = {"preco_pagamento": 70, "valor_receber_unitario": 100, "quantidade_padrao": 1}
        pay, receive = database.calculate_entry(service, None, 1, 0, 0)
        self.assertEqual(pay, 70)
        self.assertEqual(receive, 0)

    def test_new_entry_is_calculated_and_persisted(self):
        with database.connect() as db:
            result = database.create_entry(db, {
                "obra_id": 1, "data": "2026-09-19", "funcionario_id": 1,
                "servico_id": 1, "tipo": "produção", "quantidade_m2": 2,
                "extra": 10, "desconto": 5,
            })
            self.assertEqual(result["valor_pagar"], 43)
            self.assertEqual(result["valor_receber"], 75.68)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0], 6505)

    def test_entry_deletion_is_soft_audited_and_blocks_measured_records(self):
        with database.connect() as db:
            created = database.create_entry(db, {
                "obra_id": 1, "data": "2026-09-19", "funcionario_id": 1,
                "servico_id": 1, "tipo": "produção", "quantidade_m2": 1,
            }, user_id=1)
            result = database.deactivate_entry(db, created["id"], "Lançamento duplicado", user_id=1)
            row = db.execute("SELECT ativo FROM lancamentos WHERE id=?", (created["id"],)).fetchone()
            audit = db.execute("SELECT acao,detalhes FROM auditoria WHERE entidade='lancamento' AND entidade_id=? ORDER BY id DESC", (created["id"],)).fetchone()
            self.assertTrue(result["historico_preservado"])
            self.assertEqual(row["ativo"], 0)
            self.assertEqual(audit["acao"], "excluido")
            measured = db.execute("SELECT id FROM lancamentos WHERE pms_numero IS NOT NULL LIMIT 1").fetchone()
            if measured:
                with self.assertRaisesRegex(ValueError, "não pode ser excluído"):
                    database.deactivate_entry(db, measured["id"], "Tentativa inválida", user_id=1)

    def test_new_entry_rejects_invalid_type_and_negative_values(self):
        base = {
            "obra_id": 1, "data": "2026-09-19", "funcionario_id": 1,
            "servico_id": 1, "tipo": "produção", "quantidade_m2": 1,
        }
        with database.connect() as db:
            with self.assertRaisesRegex(ValueError, "Tipo de lançamento inválido"):
                database.create_entry(db, {**base, "tipo": "pagamento"})
            with self.assertRaisesRegex(ValueError, "Quantidade não pode ter valor negativo"):
                database.create_entry(db, {**base, "quantidade_m2": -1})
            with self.assertRaisesRegex(ValueError, "Extra deve ser um número válido"):
                database.create_entry(db, {**base, "extra": "inválido"})

    def test_entry_frontend_exposes_only_valid_creation_flow(self):
        source = (Path(__file__).parent / "app-system.js").read_text(encoding="utf-8")
        self.assertIn("function entryActionButton()", source)
        self.assertIn("function activeEntryEmployees()", source)
        self.assertIn("function activeEntryServices()", source)
        self.assertIn('name="ap"', source)

    def test_default_users_and_secure_passwords(self):
        with database.connect() as db:
            users = db.execute("SELECT * FROM usuarios ORDER BY id").fetchall()
            self.assertEqual(len(users), 2)
            self.assertEqual(users[0]["role"], "admin")
            self.assertEqual(users[1]["role"], "usuario")
            self.assertNotIn("Admin@123", users[0]["password_hash"])
            self.assertTrue(database.verify_password("Admin@123", users[0]["password_hash"]))

    def test_login_creates_session_and_password_change_revokes_it(self):
        with database.connect() as db:
            token, csrf, user = database.authenticate(db, "admin", "Admin@123", "127.0.0.1")
            self.assertEqual(user["role"], "admin")
            self.assertTrue(csrf)
            session = database.session_user(db, token)
            self.assertEqual(session["username"], "admin")
            database.change_password(db, user["id"], "Admin@123", "Nova@Senha1")
            self.assertIsNone(database.session_user(db, token))
            password_audit = db.execute(
                "SELECT * FROM auditoria WHERE entidade='usuario' AND acao='senha_alterada' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(password_audit)
            self.assertNotIn("Admin@123", str(dict(password_audit)))
            self.assertNotIn("Nova@Senha1", str(dict(password_audit)))
            token2, _, _ = database.authenticate(db, "admin", "Nova@Senha1", "127.0.0.1")
            self.assertTrue(database.session_user(db, token2))

    def test_invalid_password_is_rejected(self):
        with database.connect() as db:
            with self.assertRaises(PermissionError):
                database.authenticate(db, "admin", "senha-errada", "127.0.0.2")


if __name__ == "__main__":
    unittest.main()
