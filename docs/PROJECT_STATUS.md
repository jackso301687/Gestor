# Estado do projeto

## Implementado

- Gestor web e apontamento de campo separados.
- Catálogos, produção, qualidade, medição e pagamento preservados como fluxos distintos.
- Entrada serverless Python para `/api` na Vercel.
- Seleção de banco por `DATABASE_URL`: PostgreSQL em produção e SQLite local.
- Tradutor das consultas legadas para o dialeto PostgreSQL.
- Migração única do banco SQLite operacional para PostgreSQL.
- Cookies seguros automaticamente na Vercel.
- PDF de proposta com gerador alternativo quando Chrome não estiver disponível.
- Backup e restauração SQLite bloqueados no modo PostgreSQL; devem ser feitos pelo provedor.

## Verificação concluída

- 62 testes Python passaram com a base operacional de referência.
- 7 testes do apontamento passaram.
- Sintaxe Python, JavaScript e `vercel.json` validadas.
- Schema operacional com 38 tabelas convertido e ordenado por dependências.

## Pendente de infraestrutura

- Criar um PostgreSQL hospedado vazio.
- Executar o migrador com a `DATABASE_URL` real.
- Rodar testes de fumaça contra o PostgreSQL hospedado.
- Configurar `DATABASE_URL` na Vercel e validar o domínio publicado.

O projeto só deve receber usuários de produção depois dessas quatro etapas.
