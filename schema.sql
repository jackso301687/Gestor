PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'usuario')),
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
  deve_trocar_senha INTEGER NOT NULL DEFAULT 1 CHECK (deve_trocar_senha IN (0, 1)),
  ultimo_login TEXT,
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  ip TEXT,
  expira_em TEXT NOT NULL,
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tentativas_login (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  ip TEXT,
  sucesso INTEGER NOT NULL DEFAULT 0,
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS obras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL UNIQUE,
  cliente_contratante TEXT,
  fornecedor TEXT,
  engenheiro_responsavel TEXT,
  endereco TEXT,
  data_inicio TEXT,
  pms_atual INTEGER,
  status TEXT NOT NULL DEFAULT 'ativa'
);

CREATE TABLE IF NOT EXISTS funcionarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  nome TEXT NOT NULL,
  profissao TEXT,
  situacao TEXT DEFAULT 'efetivado',
  telefone TEXT,
  documento TEXT,
  data_admissao TEXT,
  UNIQUE (obra_id, nome)
);

CREATE TABLE IF NOT EXISTS servicos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  nome_interno TEXT NOT NULL,
  descricao_pms TEXT,
  preco_pagamento REAL NOT NULL DEFAULT 0,
  unidade TEXT,
  quantidade_padrao REAL NOT NULL DEFAULT 1,
  valor_receber_unitario REAL NOT NULL DEFAULT 0,
  UNIQUE (obra_id, nome_interno)
);

CREATE TABLE IF NOT EXISTS lancamentos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  data TEXT NOT NULL,
  dia_semana TEXT,
  mes TEXT,
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  tipo TEXT NOT NULL DEFAULT 'produção',
  servico_id INTEGER NOT NULL REFERENCES servicos(id),
  quantidade_m2 REAL,
  horas_trabalhadas REAL,
  desconto REAL NOT NULL DEFAULT 0,
  extra REAL NOT NULL DEFAULT 0,
  bloco TEXT,
  ap TEXT,
  casa TEXT,
  valor_pagar REAL NOT NULL DEFAULT 0,
  valor_receber REAL NOT NULL DEFAULT 0,
  obs TEXT,
  status TEXT NOT NULL DEFAULT 'calculado',
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS previsao_pms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  descricao_pms TEXT,
  valor_unitario REAL NOT NULL DEFAULT 0,
  quantidade REAL NOT NULL DEFAULT 0,
  valor_total REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS descontos_servico (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  descricao_pms TEXT NOT NULL,
  valor_receber REAL NOT NULL DEFAULT 0,
  valor_desconto REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reembolso_justificativas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  pms_numero INTEGER,
  descricao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medicoes_pms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  numero INTEGER NOT NULL,
  periodo_inicio TEXT NOT NULL,
  periodo_fim TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'rascunho',
  valor_bruto REAL NOT NULL DEFAULT 0,
  valor_desconto REAL NOT NULL DEFAULT 0,
  valor_liquido REAL NOT NULL DEFAULT 0,
  observacao TEXT,
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (obra_id, numero)
);

CREATE TABLE IF NOT EXISTS pagamentos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  periodo_inicio TEXT NOT NULL,
  periodo_fim TEXT NOT NULL,
  valor_bruto REAL NOT NULL,
  valor_desconto REAL NOT NULL,
  valor_extra REAL NOT NULL,
  valor_liquido REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendente',
  data_pagamento TEXT,
  UNIQUE (obra_id, funcionario_id, periodo_inicio, periodo_fim)
);

CREATE TABLE IF NOT EXISTS auditoria (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entidade TEXT NOT NULL,
  entidade_id INTEGER,
  acao TEXT NOT NULL,
  detalhes TEXT,
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lancamentos_obra_data ON lancamentos(obra_id, data);
CREATE INDEX IF NOT EXISTS idx_lancamentos_funcionario ON lancamentos(funcionario_id);
CREATE INDEX IF NOT EXISTS idx_lancamentos_servico ON lancamentos(servico_id);
CREATE INDEX IF NOT EXISTS idx_sessoes_token ON sessoes(token_hash);
CREATE INDEX IF NOT EXISTS idx_tentativas_login ON tentativas_login(username, ip, criado_em);

CREATE VIEW IF NOT EXISTS vw_total_pagar_funcionario AS
SELECT l.obra_id, f.id AS funcionario_id, f.nome AS funcionario, f.profissao,
       COUNT(l.id) AS lancamentos, ROUND(SUM(l.valor_pagar), 2) AS total_a_pagar
FROM lancamentos l JOIN funcionarios f ON f.id = l.funcionario_id
GROUP BY l.obra_id, f.id, f.nome, f.profissao;

CREATE VIEW IF NOT EXISTS vw_faturamento_por_servico AS
SELECT l.obra_id, s.id AS servico_id, s.nome_interno, s.descricao_pms,
       ROUND(SUM(COALESCE(l.quantidade_m2, 0)), 4) AS quantidade,
       ROUND(SUM(l.valor_receber), 2) AS total_a_receber
FROM lancamentos l JOIN servicos s ON s.id = l.servico_id
GROUP BY l.obra_id, s.id, s.nome_interno, s.descricao_pms;

CREATE TABLE IF NOT EXISTS usuario_obras (
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  obra_id INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  PRIMARY KEY (usuario_id, obra_id)
);

CREATE TABLE IF NOT EXISTS apropriacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  data TEXT NOT NULL,
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  servico_id INTEGER NOT NULL REFERENCES servicos(id),
  pms_numero INTEGER,
  inicio_manha TEXT,
  termino_manha TEXT,
  inicio_tarde TEXT,
  termino_tarde TEXT,
  assinatura_encarregado TEXT,
  observacao TEXT,
  status TEXT NOT NULL DEFAULT 'aberta',
  criado_por INTEGER REFERENCES usuarios(id),
  atualizado_por INTEGER REFERENCES usuarios(id),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS avaliacoes_qualidade (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lancamento_id INTEGER NOT NULL UNIQUE REFERENCES lancamentos(id) ON DELETE CASCADE,
  situacao TEXT NOT NULL DEFAULT 'nao_avaliado'
    CHECK (situacao IN ('ok','pendente','nao_avaliado')),
  observacao TEXT,
  tecnico_id INTEGER REFERENCES usuarios(id),
  avaliado_em TEXT,
  atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fechamentos_pagamento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  pms_numero INTEGER,
  periodo_inicio TEXT NOT NULL,
  periodo_fim TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'rascunho'
    CHECK (status IN ('rascunho','conferido','aprovado','pago','cancelado')),
  total_servicos REAL NOT NULL DEFAULT 0,
  total_extras REAL NOT NULL DEFAULT 0,
  total_descontos REAL NOT NULL DEFAULT 0,
  total_pagar REAL NOT NULL DEFAULT 0,
  total_receber REAL NOT NULL DEFAULT 0,
  criado_por INTEGER REFERENCES usuarios(id),
  aprovado_por INTEGER REFERENCES usuarios(id),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (obra_id, pms_numero, periodo_inicio, periodo_fim)
);

CREATE TABLE IF NOT EXISTS itens_pagamento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fechamento_id INTEGER NOT NULL REFERENCES fechamentos_pagamento(id) ON DELETE CASCADE,
  lancamento_id INTEGER NOT NULL REFERENCES lancamentos(id),
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  servico_id INTEGER NOT NULL REFERENCES servicos(id),
  quantidade REAL NOT NULL DEFAULT 0,
  valor_padrao REAL NOT NULL DEFAULT 0,
  valor_ajustado REAL,
  valor_final REAL NOT NULL DEFAULT 0,
  UNIQUE (fechamento_id, lancamento_id)
);

CREATE TABLE IF NOT EXISTS ajustes_pagamento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_pagamento_id INTEGER NOT NULL REFERENCES itens_pagamento(id) ON DELETE CASCADE,
  valor_anterior REAL NOT NULL,
  valor_novo REAL NOT NULL,
  justificativa TEXT NOT NULL,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1))
);

CREATE TABLE IF NOT EXISTS descontos_pagamento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fechamento_id INTEGER NOT NULL REFERENCES fechamentos_pagamento(id) ON DELETE CASCADE,
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  tipo TEXT NOT NULL,
  descricao TEXT NOT NULL,
  valor REAL NOT NULL CHECK (valor >= 0),
  data TEXT NOT NULL,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS extras_pagamento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fechamento_id INTEGER NOT NULL REFERENCES fechamentos_pagamento(id) ON DELETE CASCADE,
  funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
  servico_id INTEGER REFERENCES servicos(id),
  descricao TEXT NOT NULL,
  justificativa TEXT NOT NULL,
  valor REAL NOT NULL CHECK (valor >= 0),
  data TEXT NOT NULL,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reembolsos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  obra_id INTEGER NOT NULL REFERENCES obras(id),
  pms_numero INTEGER,
  funcionario_id INTEGER REFERENCES funcionarios(id),
  data TEXT NOT NULL,
  descricao TEXT NOT NULL,
  valor REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pendente',
  usuario_id INTEGER REFERENCES usuarios(id),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS importacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  arquivo TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'em_revisao',
  resumo TEXT,
  criado_por INTEGER REFERENCES usuarios(id),
  criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  concluido_em TEXT
);

CREATE TABLE IF NOT EXISTS importacao_inconsistencias (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  importacao_id INTEGER NOT NULL REFERENCES importacoes(id) ON DELETE CASCADE,
  aba TEXT,
  linha INTEGER,
  campo TEXT,
  valor TEXT,
  mensagem TEXT NOT NULL,
  resolvido INTEGER NOT NULL DEFAULT 0 CHECK (resolvido IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_apropriacoes_data ON apropriacoes(obra_id, data);
CREATE INDEX IF NOT EXISTS idx_apropriacoes_funcionario ON apropriacoes(funcionario_id, data);
CREATE INDEX IF NOT EXISTS idx_qualidade_situacao ON avaliacoes_qualidade(situacao);
CREATE INDEX IF NOT EXISTS idx_fechamentos_periodo ON fechamentos_pagamento(obra_id, periodo_inicio, periodo_fim);
CREATE INDEX IF NOT EXISTS idx_auditoria_entidade ON auditoria(entidade, entidade_id, criado_em);
