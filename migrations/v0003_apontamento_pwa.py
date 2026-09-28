"""Stores JSON do PWA, isoladas por obra e sem mistura com medição/pagamento."""

VERSION = "0003"
NAME = "apontamento_pwa_records"


def upgrade(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS apontamento_records (
          obra_id INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (kind IN ('employees','services','locations','appointments','settings','audit')),
          id TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          data TEXT NOT NULL CHECK (json_valid(data)),
          atualizado_por INTEGER REFERENCES usuarios(id),
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (obra_id, kind, id)
        );

        CREATE INDEX IF NOT EXISTS idx_apontamento_records_obra_kind_updated
          ON apontamento_records(obra_id, kind, updated_at);
        """
    )
