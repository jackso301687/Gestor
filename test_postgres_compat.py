import sqlite3
import unittest
from pathlib import Path

from postgres_compat import CompatRow, translate_sql
from scripts.migrate_sqlite_to_postgres import postgres_ddl, source_objects, table_order
from server import PMSHandler


class PostgreSQLCompatibilityTest(unittest.TestCase):
    def test_rows_match_sqlite_access_patterns(self):
        row = CompatRow(["id", "nome"], [7, "ATLANTA"])
        self.assertEqual(row[0], 7)
        self.assertEqual(row["nome"], "ATLANTA")
        self.assertEqual(tuple(row), (7, "ATLANTA"))
        self.assertEqual(dict(row), {"id": 7, "nome": "ATLANTA"})

    def test_queries_translate_parameters_conflicts_and_generated_ids(self):
        select, returning = translate_sql("SELECT * FROM usuarios WHERE username=? COLLATE NOCASE")
        self.assertIn("LOWER(username)=LOWER(%s)", select)
        self.assertIsNone(returning)
        insert, returning = translate_sql("INSERT OR IGNORE INTO usuarios(nome) VALUES(?)")
        self.assertIn("ON CONFLICT DO NOTHING", insert)
        self.assertTrue(insert.endswith("RETURNING id"))
        self.assertEqual(returning, "usuarios")

    def test_sqlite_functions_translate_for_postgresql(self):
        query, _ = translate_sql(
            "SELECT ROUND(SUM(valor_pagar),2),GROUP_CONCAT(DISTINCT obs) "
            "FROM lancamentos WHERE date(criado_em)>=?"
        )
        self.assertIn("ROUND(CAST(SUM(valor_pagar) AS numeric), 2)", query)
        self.assertIn("STRING_AGG(DISTINCT CAST(obs AS text), ',')", query)
        self.assertIn("CAST(criado_em AS date)>=%s", query)

    def test_operational_schema_is_ordered_and_convertible(self):
        source_path = Path("/home/jackson/Área de trabalho/Gestor/pms.sqlite3")
        if not source_path.exists():
            self.skipTest("Base operacional de referência não está disponível")
        with sqlite3.connect(source_path) as source:
            objects = source_objects(source)
            tables = table_order(source, [name for kind, name, _ in objects if kind == "table"])
            converted = [postgres_ddl(sql) for kind, _, sql in objects if kind == "table"]
        self.assertLess(tables.index("obras"), tables.index("funcionarios"))
        self.assertLess(tables.index("funcionarios"), tables.index("lancamentos"))
        self.assertTrue(all("AUTOINCREMENT" not in ddl.upper() for ddl in converted))
        self.assertTrue(all("COLLATE NOCASE" not in ddl.upper() for ddl in converted))

    def test_vercel_rewrite_restores_original_api_path_and_query(self):
        handler = object.__new__(PMSHandler)
        handler.path = "/api?__path=auth%2Fme&obra_id=3"
        parsed = handler.parsed_url()
        self.assertEqual(parsed.path, "/api/auth/me")
        self.assertEqual(parsed.query, "obra_id=3")


if __name__ == "__main__":
    unittest.main()
