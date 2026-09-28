"""Colunas operacionais introduzidas após o schema inicial importado."""

VERSION = "0001"
NAME = "operational_columns"


def _has_column(connection, table, column):
    return any(row["name"] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _add_column(connection, table, column, definition):
    if not _has_column(connection, table, column):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upgrade(connection):
    additions = {
        "usuarios": [
            ("perfil", "TEXT"),
        ],
        "funcionarios": [
            ("ativo", "INTEGER NOT NULL DEFAULT 1"),
            ("atualizado_em", "TEXT"),
        ],
        "servicos": [
            ("ativo", "INTEGER NOT NULL DEFAULT 1"),
            ("atualizado_em", "TEXT"),
        ],
        "lancamentos": [
            ("pms_numero", "INTEGER"),
            ("criado_por", "INTEGER"),
            ("atualizado_por", "INTEGER"),
            ("atualizado_em", "TEXT"),
        ],
        "auditoria": [
            ("usuario_id", "INTEGER"),
            ("antes", "TEXT"),
            ("depois", "TEXT"),
            ("ip", "TEXT"),
        ],
    }
    for table, columns in additions.items():
        for column, definition in columns:
            _add_column(connection, table, column, definition)

    connection.execute(
        "UPDATE usuarios SET perfil=CASE role WHEN 'admin' THEN 'admin' ELSE 'operador' END "
        "WHERE perfil IS NULL"
    )
    connection.execute(
        "UPDATE funcionarios SET ativo=CASE "
        "WHEN lower(COALESCE(situacao,'')) IN ('desativado','inativo','demitido') THEN 0 ELSE 1 END"
    )
    connection.execute("UPDATE servicos SET ativo=1 WHERE ativo IS NULL")
