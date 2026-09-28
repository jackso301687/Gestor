"""Configurações do sistema, obra, preferências pessoais e vínculo do campo."""
import json

VERSION = "0004"
NAME = "application_settings_and_field_links"


def _has_column(connection, table, column):
    return any(row["name"] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _create_tables(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS configuracoes_empresa (
          id INTEGER PRIMARY KEY CHECK (id=1),
          nome TEXT NOT NULL DEFAULT '',
          cnpj TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '',
          telefone TEXT NOT NULL DEFAULT '',
          endereco TEXT NOT NULL DEFAULT '',
          logo_mime TEXT,
          logo_dados BLOB,
          atualizado_por INTEGER REFERENCES usuarios(id),
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS configuracoes_obra (
          obra_id INTEGER PRIMARY KEY REFERENCES obras(id) ON DELETE CASCADE,
          responsavel_apontamento TEXT NOT NULL DEFAULT '',
          bloquear_datas_futuras INTEGER NOT NULL DEFAULT 1 CHECK (bloquear_datas_futuras IN (0,1)),
          politica_coletivo TEXT NOT NULL DEFAULT 'dividir_igualmente'
            CHECK (politica_coletivo IN ('dividir_igualmente','distribuicao_manual','nao_importar')),
          atualizado_por INTEGER REFERENCES usuarios(id),
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS preferencias_usuario (
          usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
          obra_padrao_id INTEGER REFERENCES obras(id) ON DELETE SET NULL,
          tela_inicial TEXT NOT NULL DEFAULT 'overview'
            CHECK (tela_inicial IN ('overview','entries','quality','pms','payments')),
          linhas_por_pagina INTEGER NOT NULL DEFAULT 25 CHECK (linhas_por_pagina IN (25,50,100)),
          atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS apontamento_vinculos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          obra_id INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
          tipo_origem TEXT NOT NULL CHECK (tipo_origem IN ('employees','services')),
          id_origem TEXT NOT NULL,
          id_destino INTEGER NOT NULL,
          criado_por INTEGER REFERENCES usuarios(id),
          criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (obra_id, tipo_origem, id_origem)
        );

        CREATE INDEX IF NOT EXISTS idx_apontamento_vinculos_destino
          ON apontamento_vinculos(obra_id,tipo_origem,id_destino);

        INSERT OR IGNORE INTO configuracoes_empresa(id) VALUES(1);
        INSERT OR IGNORE INTO configuracoes_obra(obra_id)
          SELECT id FROM obras;
        """
    )


def backfill_legacy_settings(connection):
    """Copia defaults legados uma vez, sem substituir mudanças administrativas posteriores."""
    rows = connection.execute(
        """SELECT obra_id,data,updated_at FROM apontamento_records
           WHERE kind='settings' AND id='app' ORDER BY updated_at DESC"""
    ).fetchall()
    for row in rows:
        try:
            legacy = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        company = (legacy.get("companyName") or "").strip()
        if company:
            connection.execute(
                "UPDATE configuracoes_empresa SET nome=? WHERE id=1 AND nome='' AND atualizado_por IS NULL",
                (company[:180],),
            )
        connection.execute(
            """UPDATE configuracoes_obra
               SET responsavel_apontamento=?, bloquear_datas_futuras=?
               WHERE obra_id=? AND responsavel_apontamento='' AND atualizado_por IS NULL""",
            (
                str(legacy.get("responsible") or "")[:180],
                int(legacy.get("blockFutureDates", True) is not False),
                row["obra_id"],
            ),
        )


def upgrade(connection):
    _create_tables(connection)
    for table, column, definition in (
        ("lancamentos", "apontamento_origem_id", "TEXT"),
        ("lancamentos", "funcionario_origem_id", "TEXT"),
    ):
        if not _has_column(connection, table, column):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_lancamento_origem_campo
           ON lancamentos(obra_id,apontamento_origem_id,funcionario_origem_id)
           WHERE apontamento_origem_id IS NOT NULL AND funcionario_origem_id IS NOT NULL"""
    )
    backfill_legacy_settings(connection)
