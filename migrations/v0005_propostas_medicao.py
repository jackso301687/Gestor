"""Propostas de medição editáveis, separadas de produção, PMS e pagamento."""

VERSION = "0005"
NAME = "propostas_medicao"


def upgrade(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS propostas_medicao (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          obra_id INTEGER NOT NULL REFERENCES obras(id),
          pms_numero INTEGER,
          periodo_inicio TEXT NOT NULL,
          periodo_fim TEXT NOT NULL,
          empresa TEXT NOT NULL DEFAULT 'ES Souza Construções',
          data_proposta TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'rascunho' CHECK (status IN ('rascunho','emitida')),
          criado_por INTEGER REFERENCES usuarios(id),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS proposta_servicos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          proposta_id INTEGER NOT NULL REFERENCES propostas_medicao(id) ON DELETE CASCADE,
          servico_id INTEGER REFERENCES servicos(id),
          nome_servico TEXT NOT NULL,
          ordem INTEGER NOT NULL,
          UNIQUE (proposta_id, ordem)
        );
        CREATE TABLE IF NOT EXISTS proposta_itens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          proposta_servico_id INTEGER NOT NULL REFERENCES proposta_servicos(id) ON DELETE CASCADE,
          bloco TEXT,
          casa TEXT NOT NULL DEFAULT '',
          ordem INTEGER NOT NULL,
          UNIQUE (proposta_servico_id, ordem)
        );
        CREATE INDEX IF NOT EXISTS idx_propostas_medicao_obra_periodo
          ON propostas_medicao(obra_id, periodo_inicio, periodo_fim);
        CREATE INDEX IF NOT EXISTS idx_proposta_servicos_proposta
          ON proposta_servicos(proposta_id, ordem);
        CREATE INDEX IF NOT EXISTS idx_proposta_itens_servico
          ON proposta_itens(proposta_servico_id, ordem);
        """
    )
