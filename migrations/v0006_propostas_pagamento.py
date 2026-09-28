"""Propostas de pagamento editáveis, sem alterar lançamentos ou medições."""

VERSION = "0006"
NAME = "propostas_pagamento"


def upgrade(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS propostas_pagamento (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          obra_id INTEGER NOT NULL REFERENCES obras(id),
          pms_numero INTEGER,
          periodo_inicio TEXT NOT NULL,
          periodo_fim TEXT NOT NULL,
          observacao TEXT NOT NULL DEFAULT '',
          criado_por INTEGER REFERENCES usuarios(id),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS proposta_pagamento_itens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          proposta_id INTEGER NOT NULL REFERENCES propostas_pagamento(id) ON DELETE CASCADE,
          funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
          servico_id INTEGER NOT NULL REFERENCES servicos(id),
          bloco TEXT NOT NULL DEFAULT '',
          ap TEXT NOT NULL DEFAULT '',
          casa TEXT NOT NULL DEFAULT '',
          quantidade REAL NOT NULL DEFAULT 0,
          valor_gerado REAL NOT NULL DEFAULT 0,
          valor_pagar REAL NOT NULL DEFAULT 0,
          observacao TEXT NOT NULL DEFAULT '',
          origem_lancamentos TEXT NOT NULL DEFAULT '[]',
          ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_propostas_pagamento_obra_periodo
          ON propostas_pagamento(obra_id, periodo_inicio, periodo_fim);
        CREATE INDEX IF NOT EXISTS idx_proposta_pagamento_itens
          ON proposta_pagamento_itens(proposta_id, ativo, funcionario_id, servico_id);
        """
    )
