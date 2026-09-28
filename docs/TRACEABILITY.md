# Rastreabilidade

| Requisito | Implementação | Verificação |
|---|---|---|
| API Python na Vercel | `api/index.py`, `vercel.json` | importação e JSON validados |
| PostgreSQL em produção | `database.py`, `postgres_compat.py` | `test_postgres_compat.py` |
| Migração sem alterar a origem | `scripts/migrate_sqlite_to_postgres.py` | conversão das 38 tabelas validada |
| Apontamento separado | `campo/` | 7 testes Node aprovados |
| Produção separada de medição e pagamento | regras existentes em `database.py` e stores | suíte Python aprovada |
| Exportação sem Chrome | fallback em `proposta_export.py` | teste de PDF serverless aprovado |
| Banco e segredos fora do Git | `.gitignore`, `.vercelignore` | inventário de arquivos antes do commit |
| Backup seguro em PostgreSQL | bloqueio de backup SQLite em `settings_store.py` | validação de modo por `DATABASE_URL` |
