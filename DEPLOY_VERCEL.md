# Implantação na Vercel

## Como o projeto funciona

`server.py` é o servidor para execução local e deve continuar sendo iniciado
assim:

```bash
python3 server.py
```

Na Vercel ele **não** deve ser iniciado por `python server.py`. O arquivo
`api/index.py` expõe o `PMSHandler` como uma Vercel Function. O arquivo
`vercel.json` direciona todas as rotas `/api/*` para essa função; os arquivos
HTML, CSS, JavaScript e PWA são entregues como arquivos estáticos.

## Banco de dados antes de publicar a API

O banco atual é SQLite. Ele funciona no computador, mas não pode ser usado
como banco operacional na Vercel porque as Functions não têm disco persistente.
Antes de liberar usuários, crie um PostgreSQL hospedado e informe a conexão
por uma variável de ambiente:

```text
DATABASE_URL=postgresql://usuario:senha@host:5432/gestor?sslmode=require
PMS_SECURE_COOKIE=1
```

Cadastre ambas no painel da Vercel em **Settings → Environment Variables**,
para Production, Preview e Development conforme necessário. Nunca inclua a
URL ou senha do banco no Git.

## Estado desta cópia

Esta pasta contém a entrada serverless, a configuração de rotas e a camada de
compatibilidade PostgreSQL. Quando `DATABASE_URL` estiver definida, a aplicação
usa PostgreSQL; sem ela, continua usando SQLite para desenvolvimento local. Os
dados operacionais não foram copiados para esta pasta: o banco original
permanece em `Gestor/pms.sqlite3`.

## Migração dos dados

Com o PostgreSQL vazio criado, instale as dependências no computador e migre a
cópia operacional atual sem alterar o arquivo de origem:

```bash
cd "/home/jackson/Área de trabalho/Gestor01"
pip install -r requirements.txt
DATABASE_URL='postgresql://usuario:senha@host:5432/gestor?sslmode=require' \
  python3 scripts/migrate_sqlite_to_postgres.py \
  "/home/jackson/Área de trabalho/Gestor/pms.sqlite3"
```

O migrador transfere todas as tabelas, índices e dados, e ajusta as sequências
de identificadores no PostgreSQL. Execute-o apenas uma vez em um banco vazio.

## Validação final

Depois da migração, cadastre a mesma `DATABASE_URL` na Vercel e faça um novo
deploy. Valide login, troca de senha, sincronização do apontamento, criação de
lançamento, medição, pagamento e exportações antes de liberar usuários.
