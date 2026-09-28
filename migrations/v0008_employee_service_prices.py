"""Preço de pagamento específico por funcionário e serviço, com vigência e auditoria."""

VERSION = "0008"
NAME = "employee_service_prices"


def upgrade(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS funcionario_servico_precos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          obra_id INTEGER NOT NULL REFERENCES obras(id),
          funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
          servico_id INTEGER NOT NULL REFERENCES servicos(id),
          preco_pagamento REAL NOT NULL CHECK (preco_pagamento >= 0),
          inicio_vigencia TEXT NOT NULL,
          fim_vigencia TEXT,
          motivo TEXT NOT NULL,
          criado_por INTEGER REFERENCES usuarios(id),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          atualizado_por INTEGER REFERENCES usuarios(id),
          atualizado_em TEXT,
          versao INTEGER NOT NULL DEFAULT 1,
          ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
          UNIQUE (funcionario_id, servico_id, inicio_vigencia),
          CHECK (fim_vigencia IS NULL OR fim_vigencia >= inicio_vigencia)
        );

        CREATE INDEX IF NOT EXISTS idx_funcionario_servico_precos_vigencia
          ON funcionario_servico_precos(funcionario_id, servico_id, inicio_vigencia, fim_vigencia);
        """
    )
