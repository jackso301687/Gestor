"""Edição segura, locking otimista e histórico de preços/qualidade."""

VERSION = "0002"
NAME = "editing_and_price_history"


def _has_column(connection, table, column):
    return any(row["name"] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _add_column(connection, table, column, definition):
    if not _has_column(connection, table, column):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upgrade(connection):
    additions = {
        "obras": [
            ("codigo", "TEXT"),
            ("cnpj_contratante", "TEXT"),
            ("cidade", "TEXT"),
            ("estado", "TEXT"),
            ("previsao_termino", "TEXT"),
            ("encarregado", "TEXT"),
            ("observacoes", "TEXT"),
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
            ("atualizado_em", "TEXT"),
        ],
        "funcionarios": [
            ("data_desligamento", "TEXT"),
            ("tipo_vinculo", "TEXT"),
            ("observacoes", "TEXT"),
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
        ],
        "servicos": [
            ("codigo", "TEXT"),
            ("categoria", "TEXT"),
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
        ],
        "lancamentos": [
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
        ],
        "usuarios": [
            ("email", "TEXT"),
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
            ("atualizado_em", "TEXT"),
        ],
        "apropriacoes": [
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
        ],
        "reembolsos": [
            ("versao", "INTEGER NOT NULL DEFAULT 1"),
            ("atualizado_em", "TEXT"),
        ],
    }
    for table, columns in additions.items():
        for column, definition in columns:
            _add_column(connection, table, column, definition)

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS servico_precos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          servico_id INTEGER NOT NULL REFERENCES servicos(id),
          obra_id INTEGER NOT NULL REFERENCES obras(id),
          preco_pagamento REAL NOT NULL CHECK (preco_pagamento >= 0),
          valor_receber_unitario REAL NOT NULL CHECK (valor_receber_unitario >= 0),
          inicio_vigencia TEXT NOT NULL,
          fim_vigencia TEXT,
          motivo TEXT NOT NULL,
          criado_por INTEGER REFERENCES usuarios(id),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          atualizado_por INTEGER REFERENCES usuarios(id),
          atualizado_em TEXT,
          versao INTEGER NOT NULL DEFAULT 1,
          ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
          UNIQUE (servico_id, inicio_vigencia),
          CHECK (fim_vigencia IS NULL OR fim_vigencia >= inicio_vigencia)
        );

        CREATE TABLE IF NOT EXISTS qualidade_historico (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lancamento_id INTEGER NOT NULL REFERENCES lancamentos(id) ON DELETE CASCADE,
          situacao TEXT NOT NULL,
          observacao TEXT,
          tecnico_id INTEGER REFERENCES usuarios(id),
          registrado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_servico_precos_vigencia
          ON servico_precos(servico_id, inicio_vigencia, fim_vigencia);
        CREATE INDEX IF NOT EXISTS idx_qualidade_historico_lancamento
          ON qualidade_historico(lancamento_id, registrado_em);
        """
    )
