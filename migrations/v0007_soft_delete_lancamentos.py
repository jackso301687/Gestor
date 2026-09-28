"""Permite excluir lançamentos operacionalmente sem apagar histórico."""

VERSION = "0007"
NAME = "soft_delete_lancamentos"


def upgrade(connection):
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(lancamentos)")}
    if "ativo" not in columns:
        connection.execute("ALTER TABLE lancamentos ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1))")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_lancamentos_ativos ON lancamentos(obra_id, ativo, data)")
