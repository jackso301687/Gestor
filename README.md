# Gestor de Campo

ERP de obra com módulos separados para gestão e apontamento de campo.

## Desenvolvimento local

```bash
python3 server.py
```

Abra `http://127.0.0.1:8080`.

## Vercel e PostgreSQL

A função Python está em `api/index.py`. Em produção, configure:

```text
DATABASE_URL=postgresql://usuario:senha@host:5432/gestor?sslmode=require
PMS_SECURE_COOKIE=1
```

Migre o banco SQLite uma única vez seguindo `DEPLOY_VERCEL.md`. O arquivo
SQLite, backups e credenciais não devem ser enviados ao Git.

## Verificação

```bash
node test_field.mjs
python3 -m unittest -v test_postgres_compat.py test_system.py test_http.py
```

Os testes completos usam uma base de referência local e não publicam os dados
operacionais.
