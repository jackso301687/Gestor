const app = document.querySelector('#app');
const dialog = document.querySelector('#modal');
function currentFortnightRange(now = new Date()) {
  const year = now.getFullYear();
  const month = now.getMonth();
  const firstDay = now.getDate() <= 15 ? 1 : 16;
  const lastDay = now.getDate() <= 15 ? 15 : new Date(year, month + 1, 0).getDate();
  const iso = day => `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  return { start: iso(firstDay), end: iso(lastDay) };
}
const defaultPeriod = currentFortnightRange();
const state = { obraId: Number(sessionStorage.getItem('pms_work_id')) || 1, works: [], view: 'overview', settingsSection: 'account', settingsAuditPage: 1, settingsAuditFilters: {}, pageSize: 25, page: 1, qualityPage: 1, search: '', qualityFilter: 'todos', bootstrap: null, currentUser: null, csrf: sessionStorage.getItem('pms_csrf') || '', start: defaultPeriod.start, end: defaultPeriod.end, availableStart: '', availableEnd: '', rows: {} };
const initialRoute = new URLSearchParams(window.location.search);

const money = value => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value || 0));
const number = value => new Intl.NumberFormat('pt-BR').format(Number(value || 0));
const dateBR = value => value ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`)) : '—';
const dateTimeBR = value => {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]} ${match[4]}:${match[5]}` : (value ? dateBR(value) : '—');
};
const escapeHTML = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const profileNames = { admin: 'Administrador', engenharia: 'Engenharia', qualidade: 'Qualidade', financeiro: 'Responsável por pagamentos', operador: 'Operador' };
const statusNames = {
  em_conferencia: 'Em conferência', nao_avaliado: 'Sem avaliação',
  rascunho: 'Rascunho', aberto: 'Aberto', aberta: 'Aberta', aprovado: 'Aprovado',
  aprovada: 'Aprovada', pendente: 'Com pendência', cancelado: 'Cancelado',
  cancelada: 'Cancelada', finalizada: 'Finalizada', concluido: 'Concluído',
  efetivado: 'Efetivado', efetivada: 'Efetivada', afastado: 'Afastado',
  desligado: 'Desligado', planejamento: 'Planejamento', paralisada: 'Paralisada',
  reembolsado: 'Reembolsado', ok: 'Aprovado', diaria: 'Diária',
};
const auditEntityNames = {
  apontamento_campo: 'Apontamento de campo', apontamento_vinculo: 'Vínculo do campo',
  apropriacao: 'Apropriação', banco: 'Banco de dados', configuracoes_empresa: 'Configurações da empresa',
  configuracoes_obra: 'Configurações da obra', fechamento: 'Fechamento', funcionario: 'Funcionário',
  funcionarios: 'Funcionário', item_pagamento: 'Item do fechamento', lancamento: 'Lançamento',
  medicao_pms: 'Medição PMS', obras: 'Obra', preferencias_usuario: 'Preferências',
  previsao_pms: 'Previsão PMS', qualidade: 'Qualidade', reembolso: 'Reembolso',
  proposta_medicao: 'Proposta de Medição', proposta_servico: 'Serviço da proposta', proposta_item: 'Local da proposta',
  servico: 'Serviço', servicos: 'Serviço', servico_preco: 'Preço do serviço',
  funcionario_servico_preco: 'Preço individual de serviço',
  sessao: 'Sessão', usuario: 'Usuário',
};
const auditActionNames = {
  alterada: 'Alterada', alteradas: 'Alteradas', alterado: 'Alterado', ajustado: 'Ajustado',
  avaliada: 'Avaliada', corrigido: 'Corrigido', criada: 'Criada', criado: 'Criado',
  dados_pessoais_alterados: 'Dados pessoais alterados', desativado: 'Desativado',
  gerada: 'Gerada', gerado: 'Gerado', importado_para_lancamentos: 'Importado para produção',
  login_sucesso: 'Acesso realizado', logout: 'Sessão encerrada',
  outras_sessoes_encerradas: 'Outras sessões encerradas', removido: 'Removido',
  servicos_reordenados: 'Serviços reordenados', bloco_alterado: 'Bloco alterado',
  restaurado: 'Restaurado', retificada: 'Retificada', retificado: 'Retificado',
};
const readableName = (value, names) => names[String(value || '').toLowerCase()] || String(value || '—').replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());
const can = permission => state.currentUser?.permissoes?.includes('*') || state.currentUser?.permissoes?.includes(permission);

function withPeriod(path) {
  const params = new URLSearchParams();
  if (state.start) params.set('start', state.start);
  if (state.end) params.set('end', state.end);
  if (!params.size) return path;
  return `${path}${path.includes('?') ? '&' : '?'}${params}`;
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.csrf) headers['X-CSRF-Token'] = state.csrf;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json();
  if (response.status === 401 && path !== '/api/auth/me') {
    sessionStorage.removeItem('pms_csrf');
    window.location.replace('/login.html');
    throw new Error('Sessão expirada');
  }
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir a operação.');
  return data;
}

function toast(message, type = 'success') {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.dataset.state = type;
  element.classList.add('show');
  window.setTimeout(() => element.classList.remove('show'), 3200);
}

function loading() {
  app.innerHTML = '<div class="page"><div class="stat-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div><div class="panel skeleton"></div></div>';
}

function heading(title, description, actions = '') {
  return `<div class="page-heading"><div><h1>${escapeHTML(title)}</h1><p class="lede">${escapeHTML(description)}</p></div>${actions}</div>`;
}

function table(headers, rows, empty = 'Nenhum registro encontrado.') {
  if (!rows.length) return `<div class="empty-note">◌<br><strong>${escapeHTML(empty)}</strong></div>`;
  return `<div class="table-wrap"><table class="data-table"><thead><tr>${headers.map(x => `<th>${escapeHTML(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map((cell, index) => `<td data-label="${escapeHTML(headers[index])}">${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function status(value) {
  const normalized = String(value || 'não informado').toLowerCase();
  const kind = normalized.includes('deslig') || normalized.includes('pend') ? 'warning' : normalized.includes('não') ? 'neutral' : '';
  return `<span class="pill ${kind}">${escapeHTML(readableName(value || 'não informado', statusNames))}</span>`;
}

async function loadBootstrap() {
  state.bootstrap = await api(withPeriod(`/api/bootstrap?obra_id=${state.obraId}`));
  const { obra, periodo, dashboard } = state.bootstrap;
  const today = new Date().toISOString().slice(0, 10);
  state.availableStart = periodo.inicio || state.start || today;
  state.availableEnd = periodo.fim || state.end || today;
  if (!state.start && !state.end) {
    state.start = state.availableStart;
    state.end = state.availableEnd;
  }
  document.querySelector('#currentWork').textContent = obra.nome;
  document.querySelector('#projectName').textContent = obra.nome;
  document.querySelector('#projectMeta').textContent = `${readableName(obra.status || 'obra', statusNames)} · PMS ${obra.pms_atual || '—'}`;
  document.querySelector('#periodLabel').textContent = `${dateBR(state.start)} — ${dateBR(state.end)}`;
  document.querySelector('#periodButton').title = `Período aplicado: ${dateBR(state.start)} a ${dateBR(state.end)}`;
  document.querySelector('#periodButton').setAttribute('aria-label', `Escolher período. Atual: ${dateBR(state.start)} a ${dateBR(state.end)}`);
  document.querySelector('#entryCount').textContent = number(dashboard.total_lancamentos);
}

async function loadWorks() {
  state.works = await api('/api/works');
  if (!state.works.length) throw new Error('Nenhuma obra foi autorizada para este usuário.');
  if (!state.works.some(work => Number(work.id) === Number(state.obraId))) {
    state.obraId = Number(state.works[0].id);
  }
}

function projectDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Selecionar obra</h2><p class="lede">Somente obras autorizadas para sua conta são exibidas.</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div class="project-list">${state.works.map(work => `<button class="project-option secondary-button" data-work-id="${work.id}"><span><strong>${escapeHTML(work.nome)}</strong><small>${escapeHTML(readableName(work.status || 'sem status', statusNames))} · PMS ${escapeHTML(work.pms_atual || '—')}</small></span>${Number(work.id) === Number(state.obraId) ? '<span class="pill">Atual</span>' : '<span>Selecionar →</span>'}</button>`).join('')}</div></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.querySelectorAll('[data-work-id]').forEach(button => button.addEventListener('click', async () => {
    state.obraId = Number(button.dataset.workId);
    sessionStorage.setItem('pms_work_id',String(state.obraId));
    state.rows.proposalId = null;
    state.rows.paymentProposalId = null;
    state.page = 1;
    state.qualityPage = 1;
    state.search = '';
    dialog.close();
    loading();
    try { await loadBootstrap(); await render(state.view); }
    catch (error) { toast(error.message, 'error'); }
  }));
}

function periodDialog() {
  dialog.innerHTML = `<div class="modal period-modal"><div class="modal-head"><div><h2>Período dos dados</h2><p class="lede">A seleção será aplicada aos dados operacionais, financeiros, medições e relatórios desta obra.</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="periodForm"><div class="form-grid">
    <div class="field"><label>Data inicial *</label><input name="start" type="date" required value="${escapeHTML(state.start)}"></div>
    <div class="field"><label>Data final *</label><input name="end" type="date" required value="${escapeHTML(state.end)}"></div>
    <div class="field full"><small class="field-help">Histórico disponível nesta obra: ${dateBR(state.availableStart)} até ${dateBR(state.availableEnd)}.</small></div>
  </div><div class="modal-footer period-footer"><button type="button" class="text-button" data-all-period>Todo o histórico</button><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button" type="submit">Aplicar período</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.querySelector('[data-all-period]').addEventListener('click', () => {
    dialog.querySelector('[name="start"]').value = state.availableStart;
    dialog.querySelector('[name="end"]').value = state.availableEnd;
  });
  dialog.querySelector('#periodForm').addEventListener('submit', async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (!values.start || !values.end || values.start > values.end) {
      document.querySelector('#modalError').innerHTML = '<div class="modal-error">Informe um período válido. A data inicial não pode ser posterior à data final.</div>';
      return;
    }
    state.start = values.start;
    state.end = values.end;
    state.page = 1;
    state.qualityPage = 1;
    state.rows = {};
    dialog.close();
    loading();
    try {
      await loadBootstrap();
      await render(state.view);
      toast(`Período aplicado: ${dateBR(state.start)} a ${dateBR(state.end)}.`);
    } catch (error) { toast(error.message, 'error'); }
  });
}

async function loadCurrentUser() {
  const data = await api('/api/auth/me');
  state.currentUser = data.user;
  state.csrf = data.csrf_token;
  sessionStorage.setItem('pms_csrf', state.csrf);
  const initials = data.user.nome.split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
  document.querySelector('#userAvatar').textContent = initials;
  document.querySelector('#userName').textContent = data.user.nome;
  document.querySelector('#userRole').textContent = profileNames[data.user.perfil] || data.user.perfil;
  document.querySelectorAll('.admin-only').forEach(element => { element.hidden = data.user.perfil !== 'admin'; });
}

function activeEntryEmployees() {
  return (state.bootstrap?.funcionarios || []).filter(item => item.ativo !== 0 && !String(item.situacao || '').toLowerCase().includes('deslig'));
}

function activeEntryServices() {
  return (state.bootstrap?.servicos || []).filter(item => item.ativo !== 0);
}

function entryActionButton() {
  if (!can('lancamentos')) return '<button class="primary-button" disabled title="Seu perfil não pode criar lançamentos">+ Novo lançamento</button>';
  if (!activeEntryEmployees().length) return '<button class="primary-button" disabled title="Cadastre ou reative um colaborador antes de lançar">+ Novo lançamento</button>';
  if (!activeEntryServices().length) return '<button class="primary-button" disabled title="Cadastre ou reative um serviço antes de lançar">+ Novo lançamento</button>';
  return '<button class="primary-button" data-action="new-entry">+ Novo lançamento</button>';
}

async function renderOverview() {
  const { obra, dashboard: stats } = state.bootstrap;
  const [recent, quality, pms] = await Promise.all([
    api(withPeriod(`/api/entries?obra_id=${state.obraId}&limit=5`)),
    api(withPeriod(`/api/quality?obra_id=${state.obraId}&situacao=todos`)),
    api(withPeriod(`/api/pms?obra_id=${state.obraId}`)),
  ]);
  state.rows.entries = recent.items;
  const qualityTotals = quality.totals || {};
  const notReviewed = Number(qualityTotals.nao_avaliado || 0);
  const hasIssue = Number(qualityTotals.pendente || 0);
  const measurementLabel = pms.medicao
    ? `PMS ${pms.medicao.numero} · ${readableName(pms.medicao.status, statusNames)}`
    : 'Nenhuma medição neste período';
  const overviewActions = `<div class="button-row">${state.currentUser.perfil==='admin'?'<button class="secondary-button" data-action="edit-work">Editar obra</button>':''}${entryActionButton()}</div>`;
  app.innerHTML = `<section class="page">
    ${heading(`Obra ${obra.nome}`, `Dados do período selecionado: ${dateBR(state.start)} a ${dateBR(state.end)}.`, overviewActions)}
    <section class="panel daily-work-panel" aria-labelledby="dailyWorkTitle">
      <div class="panel-heading"><div><div class="panel-title" id="dailyWorkTitle">Acompanhar no período</div><div class="panel-subtitle">Qualidade e medição desta obra</div></div></div>
      <div class="daily-work-grid">
        ${can('qualidade') ? `<button class="daily-work-card ${notReviewed ? 'is-attention' : ''}" data-open-quality="nao_avaliado"><span class="daily-work-count">${number(notReviewed)}</span><span class="daily-work-copy"><strong>Sem avaliação</strong><small>${notReviewed ? 'Revisar lançamentos' : 'Nenhum lançamento pendente'}</small></span><span class="daily-work-arrow">›</span></button>` : ''}
        ${can('qualidade') ? `<button class="daily-work-card ${hasIssue ? 'is-warning' : ''}" data-open-quality="pendente"><span class="daily-work-count">${number(hasIssue)}</span><span class="daily-work-copy"><strong>Com problema</strong><small>${hasIssue ? 'Revisar observações' : 'Nenhum problema registrado'}</small></span><span class="daily-work-arrow">›</span></button>` : ''}
        <button class="daily-work-card" data-view="payments"><span class="daily-work-count daily-work-state">${pms.medicao ? 'PMS' : '—'}</span><span class="daily-work-copy"><strong>${escapeHTML(measurementLabel)}</strong><small>${pms.medicao ? 'Abrir fechamento' : 'Abrir medições e pagamentos'}</small></span><span class="daily-work-arrow">›</span></button>
      </div>
    </section>
    <div class="stat-grid">
      <article class="stat-card"><span class="stat-label">Custo calculado</span><div class="stat-value">${money(stats.total_pagar)}</div><span class="stat-meta">${number(stats.total_lancamentos)} lançamentos no período</span></article>
      <article class="stat-card"><span class="stat-label">Valor previsto a receber</span><div class="stat-value">${money(stats.total_receber)}</div><span class="stat-meta">conforme preços dos serviços</span></article>
      <article class="stat-card"><span class="stat-label">Margem estimada</span><div class="stat-value">${money(stats.margem)}</div><span class="stat-meta">previsto a receber menos custo</span></article>
      <article class="stat-card"><span class="stat-label">Funcionários efetivados</span><div class="stat-value">${number(stats.equipe_ativa)}</div><span class="stat-meta">cadastro atual da obra</span></article>
    </div>
    <div class="dashboard-grid">
      <article class="panel"><div class="panel-heading"><div><div class="panel-title">Composição dos valores</div><div class="panel-subtitle">Lançamentos do período</div></div></div>
        <div class="summary-strip"><div class="summary-item"><span>Extras</span><strong>${money(stats.extras)}</strong></div><div class="summary-item"><span>Descontos</span><strong>${money(stats.descontos)}</strong></div><div class="summary-item"><span>PMS vigente</span><strong>${obra.pms_atual || '—'}</strong></div></div>
        <p class="metric-note">As diárias geram custo interno e não faturamento. Produções usam o preço do serviço multiplicado pela quantidade.</p>
      </article>
      <article class="panel"><div class="panel-heading"><div><div class="panel-title">Cadastros e produção</div><div class="panel-subtitle">Cadastros centrais desta obra</div></div></div>
        <div class="activity"><div class="activity-row"><span class="status-dot"></span><div class="activity-copy"><strong>${number(state.bootstrap.funcionarios.length)} funcionários no sistema</strong><small>cadastro da obra</small></div></div><div class="activity-row"><span class="status-dot"></span><div class="activity-copy"><strong>${number(state.bootstrap.servicos.length)} serviços no sistema</strong><small>catálogo da obra</small></div></div><div class="activity-row"><span class="status-dot"></span><div class="activity-copy"><strong>${number(stats.total_lancamentos)} lançamentos</strong><small>no período selecionado</small></div></div></div>
      </article>
    </div>
    <article class="panel wide-panel"><div class="panel-heading"><div><div class="panel-title">Lançamentos recentes</div><div class="panel-subtitle">Últimos registros por data</div></div><button class="text-button" data-view="entries">Ver todos →</button></div>
    ${entriesTable(recent.items)}</article>
  </section>`;
}

function entriesTable(items) {
  const editable = can('lancamentos');
  const headers = ['Data','Colaborador','Serviço','Tipo','Local','Pagar','Receber'];
  if (editable) headers.push('Ações');
  return table(headers, items.map(item => [
    dateBR(item.data), `<strong>${escapeHTML(item.funcionario)}</strong><small class="muted">${escapeHTML(item.profissao || 'sem profissão')}</small>`, escapeHTML(item.servico), status(item.tipo), escapeHTML(item.casa ? `Casa ${item.casa}` : item.bloco ? `Bloco ${item.bloco}${item.ap ? ` · AP ${item.ap}` : ''}` : '—'), `<span class="money-negative">${money(item.valor_pagar)}</span>`, `<span class="money-positive">${money(item.valor_receber)}</span>`,
    ...(editable ? [`<div class="button-row">${item.pms_numero && state.currentUser.perfil!=='admin' ? '<button class="secondary-button compact" disabled title="Apontamento já medido">Bloqueado</button>' : `<button class="secondary-button compact" data-entry-edit="${item.id}">${item.pms_numero?'Retificar':'Editar'}</button>`}${state.currentUser.perfil==='admin'?`<button class="text-button danger" data-entry-delete="${item.id}" ${item.pms_numero?'disabled title="Apontamento já medido; use retificação"':''}>Excluir</button><button class="text-button" data-history-entity="lancamento" data-history-id="${item.id}" data-history-title="Lançamento ${item.id}">Histórico</button>`:''}</div>`] : [])
  ]));
}

async function renderEntries() {
  const data = await api(withPeriod(`/api/entries?obra_id=${state.obraId}&page=${state.page}&limit=${state.pageSize}&search=${encodeURIComponent(state.search)}`));
  state.rows.entries = data.items;
  app.innerHTML = `<section class="page">${heading('Lançamentos', 'Registro diário de produção, horas, extras e descontos.', entryActionButton())}
    <div class="view-toolbar"><label class="field"><span class="workspace-label">BUSCAR</span><input class="search-input" id="entrySearch" value="${escapeHTML(state.search)}" placeholder="Nome, serviço ou casa"></label><button class="secondary-button" data-action="search">Buscar</button><a class="secondary-button" href="${withPeriod(`/api/reports/dados.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button" href="${withPeriod(`/api/reports/dados.xlsx?obra_id=${state.obraId}`)}">Excel</a><a class="secondary-button" target="_blank" href="${withPeriod(`/api/reports/dados.html?obra_id=${state.obraId}`)}">Imprimir</a></div>
    <article class="panel">${entriesTable(data.items)}<div class="table-meta"><span>${number(data.total)} registros · página ${data.page} de ${Math.max(data.pages,1)}</span><div class="pagination"><button data-page="${data.page-1}" ${data.page<=1?'disabled':''}>←</button><button data-page="${data.page+1}" ${data.page>=data.pages?'disabled':''}>→</button></div></div></article></section>`;
}

async function renderPayments() {
  const [rows,closings,pms,forecast,proposals] = await Promise.all([
    api(withPeriod(`/api/payments?obra_id=${state.obraId}`)),
    api(withPeriod(`/api/closings?obra_id=${state.obraId}`)),
    api(withPeriod(`/api/pms?obra_id=${state.obraId}`)),
    api(`/api/forecast?obra_id=${state.obraId}`),
    can('pagamentos') ? api(withPeriod(`/api/payment-proposals?obra_id=${state.obraId}`)) : Promise.resolve([]),
  ]);
  state.rows.forecast=forecast.items;
  state.rows.paymentProposals=proposals;
  const selectedId=proposals.some(item=>Number(item.id)===Number(state.rows.paymentProposalId))?state.rows.paymentProposalId:proposals[0]?.id;
  const proposal=selectedId?await api(`/api/payment-proposals/${selectedId}`):null;
  state.rows.paymentProposalId=proposal?.id||null;
  state.rows.paymentProposal=proposal;
  const total=rows.reduce((sum,item)=>sum+Number(item.valor_liquido),0);
  const pmsTitle=pms.medicao?`PMS ${pms.medicao.numero} · ${readableName(pms.medicao.status,statusNames)}`:'Sem medição registrada no período';
  const people=Object.values((proposal?.itens||[]).reduce((result,item)=>{
    const row=result[item.funcionario_id]||{nome:item.funcionario,linhas:0,gerado:0,pagar:0};
    row.linhas+=1;row.gerado+=Number(item.valor_gerado||0);row.pagar+=Number(item.valor_pagar||0);
    result[item.funcionario_id]=row;return result;
  },{})).sort((a,b)=>a.nome.localeCompare(b.nome,'pt-BR'));
  const exportLinks=proposal?`<div class="closing-export-actions"><span>Baixar esta proposta:</span><a class="secondary-button compact" href="/api/payment-proposals/${proposal.id}.pdf">PDF</a><a class="secondary-button compact" href="/api/payment-proposals/${proposal.id}.xlsx">Excel</a><a class="text-button" target="_blank" href="/api/payment-proposals/${proposal.id}.html">Imprimir</a></div>`:'';
  const grid=proposal?`<div class="payment-proposal-meta"><span>${dateBR(proposal.periodo_inicio)} a ${dateBR(proposal.periodo_fim)} · PMS ${escapeHTML(proposal.pms_numero||'—')}</span><span>${number(proposal.totais.funcionarios)} funcionários · ${number(proposal.itens.length)} linhas</span></div>
    <div class="closing-employee-summary"><div class="closing-section-caption">Resumo por funcionário</div>${table(['Funcionário','Linhas','Produzido a receber','Proposto a pagar'],people.map(item=>[escapeHTML(item.nome),number(item.linhas),money(item.gerado),`<strong>${money(item.pagar)}</strong>`]),'A proposta ainda não tem funcionários.')}</div>
    <div class="closing-section-caption">Serviços e locais · edite os campos diretamente na grade</div><div class="payment-grid-wrap"><table class="data-table payment-grid"><thead><tr><th>Funcionário</th><th>Serviço</th><th>Bloco</th><th>AP</th><th>Casa</th><th>Qtd.</th><th>Produzido a receber</th><th>Proposto a pagar</th><th>Obs.</th><th></th></tr></thead><tbody>${proposal.itens.map(item=>`<tr data-payment-row="${item.id}"><td><strong>${escapeHTML(item.funcionario)}</strong><small>${escapeHTML(item.profissao||'')}</small></td><td>${escapeHTML(item.servico)}</td><td><input aria-label="Bloco" data-payment-field="bloco" value="${escapeHTML(item.bloco)}"></td><td><input aria-label="Apartamento" data-payment-field="ap" value="${escapeHTML(item.ap)}"></td><td><input aria-label="Casa" data-payment-field="casa" value="${escapeHTML(item.casa)}"></td><td><input aria-label="Quantidade" data-payment-field="quantidade" type="number" min="0" step="0.0001" value="${escapeHTML(item.quantidade)}"></td><td class="money-cell">${money(item.valor_gerado)}</td><td><input aria-label="Valor proposto a pagar" data-payment-field="valor_pagar" type="number" min="0" step="0.01" value="${escapeHTML(item.valor_pagar)}"></td><td><input aria-label="Observação" data-payment-field="observacao" maxlength="500" value="${escapeHTML(item.observacao)}"></td><td><button class="text-button danger" data-payment-item-remove="${item.id}" aria-label="Remover linha">×</button></td></tr>`).join('')}</tbody></table></div>
    <div class="payment-grid-totals"><span>Produção gerada <strong>${money(proposal.totais.valor_gerado)}</strong></span><span>Proposta a pagar <strong>${money(proposal.totais.valor_pagar)}</strong></span></div>
    ${proposal.observacao?`<p class="field-help">${escapeHTML(proposal.observacao)}</p>`:''}`:'<div class="empty-note">Gere uma proposta para agrupar a produção por funcionário, serviço e localização.</div>';
  app.innerHTML=`<section class="page closing-page">${heading('Fechamento',`Acompanhe cada etapa da quinzena · ${dateBR(state.start)} a ${dateBR(state.end)}.`)}
    <div class="closing-flow" aria-label="Etapas do fechamento"><span>1. Produção calculada</span><span>2. Proposta ao funcionário</span><span>3. Fechamento registrado</span><span>4. Medição PMS</span></div>
    <section class="closing-stage" aria-labelledby="closing-production-title"><div class="closing-stage-head"><div><h2 id="closing-production-title">1. Produção do período</h2><p>Valores calculados a partir dos lançamentos; ainda não representam pagamento nem medição aprovada.</p></div><div class="closing-export-actions"><a class="secondary-button compact" href="${withPeriod(`/api/reports/pagamento.pdf?obra_id=${state.obraId}`)}">PDF de custos</a><a class="secondary-button compact" href="${withPeriod(`/api/reports/pagamento.xlsx?obra_id=${state.obraId}`)}">Excel de custos</a></div></div><div class="closing-metrics"><div><span>Calculado a pagar</span><strong>${money(total)}</strong></div><div><span>Produzido a receber</span><strong>${money((pms.itens||[]).reduce((sum,item)=>sum+Number(item.total_a_receber||0),0))}</strong></div></div></section>
    <section class="closing-stage" aria-labelledby="closing-proposal-title"><div class="closing-stage-head"><div><h2 id="closing-proposal-title">2. Proposta de pagamento</h2><p>Conferência por funcionário, serviço e local. Edite a grade antes de baixar o documento.</p></div><div class="button-row">${can('pagamentos')?'<button class="primary-button" data-action="new-payment-proposal">＋ Gerar proposta</button>':''}${proposal&&can('pagamentos')?'<button class="secondary-button" data-action="payment-proposal-add-item">＋ Linha</button>':''}</div></div>
      ${proposals.length?`<label class="field closing-select"><span class="workspace-label">PROPOSTA SELECIONADA</span><select id="paymentProposalSelect">${proposals.map(item=>`<option value="${item.id}" ${Number(item.id)===Number(proposal?.id)?'selected':''}>#${item.id} · ${dateBR(item.periodo_inicio)} a ${dateBR(item.periodo_fim)} · ${money(item.valor_pagar)}</option>`).join('')}</select></label>`:''}${exportLinks}${grid}</section>
    <section class="closing-stage" aria-labelledby="closing-recorded-title"><div class="closing-stage-head"><div><h2 id="closing-recorded-title">3. Fechamentos registrados</h2><p>Histórico dos fechamentos financeiros gerados para a obra e o período. Baixe o documento do fechamento desejado.</p></div>${can('pagamentos')?'<button class="secondary-button" data-action="new-closing">＋ Gerar fechamento</button>':''}</div>${table(['Período','PMS','Status','Serviços','Extras','Descontos','A pagar','A receber','Margem calculada','Documento'],closings.map(item=>[`${dateBR(item.periodo_inicio)}–${dateBR(item.periodo_fim)}`,escapeHTML(item.pms_numero||'—'),status(item.status),money(item.total_servicos),money(item.total_extras),money(item.total_descontos),money(item.total_pagar),money(item.total_receber),money(Number(item.total_receber)-Number(item.total_pagar)),`<div class="closing-row-exports"><a href="/api/reports/pagamento.pdf?obra_id=${state.obraId}&fechamento_id=${item.id}&start=${item.periodo_inicio}&end=${item.periodo_fim}">PDF</a><a href="/api/reports/pagamento.xlsx?obra_id=${state.obraId}&fechamento_id=${item.id}&start=${item.periodo_inicio}&end=${item.periodo_fim}">Excel</a></div>`]),'Nenhum fechamento registrado neste período.')}</section>
    <section class="closing-stage" aria-labelledby="closing-pms-title"><div class="closing-stage-head"><div><h2 id="closing-pms-title">4. Medição PMS</h2><p>${escapeHTML(pmsTitle)}${pms.medicao?` · ${dateBR(pms.medicao.periodo_inicio)} a ${dateBR(pms.medicao.periodo_fim)}`:''}. O PDF e o Excel abaixo mostram a produção elegível do período.</p></div><div class="button-row">${can('pms')?'<button class="secondary-button" data-action="new-pms">＋ Registrar medição</button>':''}</div></div><div class="closing-export-actions"><a class="secondary-button compact" href="${withPeriod(`/api/reports/pms.pdf?obra_id=${state.obraId}`)}">PDF da produção PMS</a><a class="secondary-button compact" href="${withPeriod(`/api/reports/pms.xlsx?obra_id=${state.obraId}`)}">Excel da produção PMS</a></div>${table(['Serviço faturável','Quantidade','Produzido a receber'],(pms.itens||[]).map(item=>[escapeHTML(item.descricao_pms||item.nome_interno),number(item.quantidade),money(item.total_a_receber)]),'Sem produção elegível no período.')}${pms.medicao?`<div class="payment-grid-totals"><span>Bruto registrado <strong>${money(pms.medicao.valor_bruto)}</strong></span><span>Desconto <strong>${money(pms.medicao.valor_desconto)}</strong></span><span>Líquido registrado <strong>${money(pms.medicao.valor_liquido)}</strong></span></div>`:''}${(pms.descontos||[]).length?`<details class="closing-details"><summary>Descontos documentados (${pms.descontos.length})</summary>${table(['Serviço','Base','Desconto'],pms.descontos.map(item=>[escapeHTML(item.descricao_pms||'—'),money(item.valor_receber),money(item.valor_desconto)]))}</details>`:''}</section>
    <section class="closing-stage" aria-labelledby="closing-forecast-title"><div class="closing-stage-head"><div><h2 id="closing-forecast-title">Previsão PMS</h2><p>Planejamento cadastral da obra, sem filtro de período próprio.</p></div><div class="closing-forecast-total"><small>Total previsto líquido</small><strong>${money(forecast.total_com_desconto)}</strong></div></div><details class="closing-details"><summary>Ver serviços previstos (${forecast.items.length})</summary>${table(['Serviço PMS','Qtd.','Total',''],forecast.items.map(item=>[escapeHTML(item.descricao_pms||'—'),number(item.quantidade),money(item.valor_total),['admin','engenharia','financeiro'].includes(state.currentUser.perfil)?`<button class="secondary-button compact" data-forecast-edit="${item.id}">Editar</button>`:'—']))}</details></section>
    </section>`;
  const paymentLabels=['Funcionário','Serviço','Bloco','AP','Casa','Quantidade','Produzido a receber','Proposto a pagar','Observação','Ação'];
  app.querySelectorAll('.payment-grid tbody tr').forEach(row=>Array.from(row.cells).forEach((cell,index)=>{cell.dataset.label=paymentLabels[index];}));
}

async function renderAppropriations() {
  const rows = await api(withPeriod(`/api/appropriations?obra_id=${state.obraId}`));
  state.rows.appropriations = rows;
  const action = can('apropriacoes') ? '<button class="primary-button" data-action="new-appropriation">＋ Nova apropriação</button>' : '';
  app.innerHTML = `<section class="page">${heading('Apropriações', 'Jornada por colaborador, serviço, data e PMS.', action)}
    <div class="view-toolbar"><a class="secondary-button" href="${withPeriod(`/api/reports/apropriacao.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button" href="${withPeriod(`/api/reports/apropriacao.xlsx?obra_id=${state.obraId}`)}">Excel</a><a class="secondary-button" target="_blank" href="${withPeriod(`/api/reports/apropriacao.html?obra_id=${state.obraId}`)}">Imprimir</a></div>
    <article class="panel">${table(['Data','Funcionário','Profissão','Serviço','Manhã','Tarde','PMS','Assinatura','Ações'], rows.map(item=>[
      dateBR(item.data),escapeHTML(item.funcionario),escapeHTML(item.profissao||'—'),escapeHTML(item.servico),
      `${escapeHTML(item.inicio_manha||'—')}–${escapeHTML(item.termino_manha||'—')}`,
      `${escapeHTML(item.inicio_tarde||'—')}–${escapeHTML(item.termino_tarde||'—')}`,
      escapeHTML(item.pms_numero||'—'),escapeHTML(item.assinatura_encarregado||'—'),can('apropriacoes')?`<button class="secondary-button compact" data-appropriation-edit="${item.id}">${item.status==='aberta'?'Editar':'Retificar'}</button>`:'—'
    ]))}</article></section>`;
}

async function renderQuality() {
  const data = await api(withPeriod(`/api/quality?obra_id=${state.obraId}&situacao=${encodeURIComponent(state.qualityFilter)}&page=${state.qualityPage}&limit=${state.pageSize}`));
  const totals = data.totals || {};
  app.innerHTML = `<section class="page">${heading('Controle de qualidade', 'Validação dos serviços que compõem a medição PMS.')}
    <div class="summary-strip"><div class="summary-item"><span>OK</span><strong>${number(totals.ok)}</strong></div><div class="summary-item"><span>Pendentes</span><strong>${number(totals.pendente)}</strong></div><div class="summary-item"><span>Não avaliados</span><strong>${number(totals.nao_avaliado)}</strong></div></div>
    <div class="filter-chips" role="group" aria-label="Filtrar serviços por situação">${[['todos','Todos'],['nao_avaliado','Sem avaliação'],['pendente','Com problema'],['ok','Aprovados']].map(([value,label])=>`<button class="filter-chip ${state.qualityFilter===value?'active':''}" data-quality-filter="${value}" aria-pressed="${state.qualityFilter===value}">${label}${value==='todos'?'':` · ${number(totals[value])}`}</button>`).join('')}</div>
    <article class="panel">${table(['Data','Funcionário','Serviço','Local','Situação','Observação','Ação'], data.items.map(item=>[
      dateBR(item.data),escapeHTML(item.funcionario),escapeHTML(item.servico),escapeHTML(item.casa?`Casa ${item.casa}`:item.bloco?`Bloco ${item.bloco} · AP ${item.ap||'—'}`:'—'),
      status(item.situacao),escapeHTML(item.observacao||'—'),`<div class="button-row">${can('qualidade')?`<button class="quality-ok-button" data-quality-ok="${item.lancamento_id}" ${item.situacao==='ok'?'disabled title="Qualidade já aprovada"':''}>✓ ${item.situacao==='ok'?'OK registrado':'OK'}</button><button class="secondary-button compact" data-quality="${item.lancamento_id}" data-quality-status="${item.situacao}" data-quality-note="${escapeHTML(item.observacao||'')}">Avaliar problema</button>`:''}<button class="text-button" data-quality-history="${item.lancamento_id}">Histórico</button></div>`
    ]))}<div class="table-meta"><span>${number(data.total)} registros nesta situação · página ${number(data.page)} de ${number(Math.max(data.pages,1))}</span><div class="pagination"><button data-quality-page="${data.page-1}" ${data.page<=1?'disabled':''} aria-label="Página anterior">←</button><button data-quality-page="${data.page+1}" ${data.page>=data.pages?'disabled':''} aria-label="Próxima página">→</button></div></div></article></section>`;
}

async function renderSummary() {
  const data = await api(withPeriod(`/api/summary?obra_id=${state.obraId}`));
  app.innerHTML = `<section class="page">${heading('Resumo operacional', 'Visão por funcionário, serviço, casa, bloco, AP, quantidade e valores.')}
    <div class="summary-strip"><div class="summary-item"><span>Registros</span><strong>${number(data.totals.linhas)}</strong></div><div class="summary-item"><span>Custo calculado</span><strong>${money(data.totals.pagar)}</strong></div><div class="summary-item"><span>Previsto a receber</span><strong>${money(data.totals.receber)}</strong></div></div>
    <div class="view-toolbar"><a class="secondary-button" href="${withPeriod(`/api/reports/resumo.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button" href="${withPeriod(`/api/reports/resumo.xlsx?obra_id=${state.obraId}`)}">Excel</a><a class="secondary-button" target="_blank" href="${withPeriod(`/api/reports/resumo.html?obra_id=${state.obraId}`)}">Imprimir</a></div>
    <article class="panel">${table(['Funcionário','Serviço','Casa','Bloco','AP','Quantidade','Extra','Desconto','Valor'],data.items.map(item=>[
      escapeHTML(item.funcionario),escapeHTML(item.servico),escapeHTML(item.casa||'—'),escapeHTML(item.bloco||'—'),escapeHTML(item.ap||'—'),number(item.quantidade),money(item.extra),money(item.desconto),`<strong>${money(item.valor)}</strong>`
    ]))}</article></section>`;
}

async function renderForecast() {
  const data = await api(`/api/forecast?obra_id=${state.obraId}`);
  state.rows.forecast = data.items;
  app.innerHTML = `<section class="page">${heading('Previsão de PMS', 'Planejamento cadastral da obra; os registros atuais não possuem data para filtro por período.')}
    <div class="summary-strip"><div class="summary-item"><span>Sem desconto</span><strong>${money(data.total_sem_desconto)}</strong></div><div class="summary-item"><span>Descontos</span><strong>${money(data.desconto)}</strong></div><div class="summary-item"><span>Com desconto</span><strong>${money(data.total_com_desconto)}</strong></div></div>
    <article class="panel">${table(['Serviço PMS','Valor unitário','Quantidade','Total','Ação'],data.items.map(item=>[escapeHTML(item.descricao_pms||'—'),money(item.valor_unitario),number(item.quantidade),`<strong>${money(item.valor_total)}</strong>`,['admin','engenharia','financeiro'].includes(state.currentUser.perfil)?`<button class="secondary-button compact" data-forecast-edit="${item.id}">Editar</button>`:'—']))}</article></section>`;
}

async function renderReimbursements() {
  const rows = await api(withPeriod(`/api/reimbursements?obra_id=${state.obraId}`));
  state.rows.reimbursements = rows;
  app.innerHTML = `<section class="page">${heading('Reembolsos', 'Justificativas, valores e situação por PMS.', can('pagamentos')?'<button class="primary-button" data-action="new-reimbursement">＋ Novo reembolso</button>':'')}
    <div class="view-toolbar"><a class="secondary-button" href="${withPeriod(`/api/reports/reembolso.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button" href="${withPeriod(`/api/reports/reembolso.xlsx?obra_id=${state.obraId}`)}">Excel</a></div>
    <article class="panel">${table(['Data','PMS','Funcionário','Descrição','Valor','Status','Responsável','Ação'],rows.map(item=>[dateBR(item.data),escapeHTML(item.pms_numero||'—'),escapeHTML(item.funcionario||'—'),escapeHTML(item.descricao),money(item.valor),status(item.status),escapeHTML(item.responsavel||'—'),can('pagamentos')?`<button class="secondary-button compact" data-reimbursement-edit="${item.id}">Editar</button>`:'—']))}</article></section>`;
}

async function renderAudit() {
  const rows = await api(withPeriod('/api/audit'));
  app.innerHTML = `<section class="page">${heading('Auditoria', 'Histórico de inclusões, alterações, remoções lógicas e responsáveis.')}
    <article class="panel">${table(['Data/hora','Usuário','Entidade','ID','Ação','Antes','Depois'],rows.map(item=>[
      escapeHTML(dateTimeBR(item.criado_em)),escapeHTML(item.usuario||'Sistema'),escapeHTML(readableName(item.entidade,auditEntityNames)),escapeHTML(item.entidade_id||'—'),status(readableName(item.acao,auditActionNames)),`<code class="audit-json">${escapeHTML(item.antes||'—')}</code>`,`<code class="audit-json">${escapeHTML(item.depois||item.detalhes||'—')}</code>`
    ]))}</article></section>`;
}

async function renderPMS() {
  const data = await api(withPeriod(`/api/pms?obra_id=${state.obraId}`));
  const m = data.medicao;
  const action = can('pms') ? '<button class="primary-button" data-action="new-pms">＋ Gerar PMS</button>' : '';
  app.innerHTML = `<section class="page">${heading(`Resumo da Medição PMS ${m?.numero || '—'}`, 'Valores propostos por serviço, descontos e situação da qualidade.', action)}
    <div class="view-toolbar"><a class="secondary-button" href="${withPeriod(`/api/reports/pms.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button" href="${withPeriod(`/api/reports/pms.xlsx?obra_id=${state.obraId}`)}">Excel</a><a class="secondary-button" target="_blank" href="${withPeriod(`/api/reports/pms.html?obra_id=${state.obraId}`)}">Imprimir</a></div>
    ${m ? `<div class="stat-grid"><article class="stat-card"><span class="stat-label">Período</span><div class="stat-value">${dateBR(m.periodo_inicio)}</div><span class="stat-meta">até ${dateBR(m.periodo_fim)}</span></article><article class="stat-card"><span class="stat-label">Valor bruto</span><div class="stat-value">${money(m.valor_bruto)}</div></article><article class="stat-card"><span class="stat-label">Descontos</span><div class="stat-value">${money(m.valor_desconto)}</div></article><article class="stat-card"><span class="stat-label">Valor líquido</span><div class="stat-value">${money(m.valor_liquido)}</div><span class="stat-meta">${escapeHTML(readableName(m.status, statusNames))}</span></article></div>` : ''}
    <article class="panel wide-panel"><div class="panel-heading"><div><div class="panel-title">Serviços faturáveis</div><div class="panel-subtitle">Agrupamento dos lançamentos por descrição PMS</div></div></div>${table(['Serviço','Quantidade','Total a receber'], data.itens.slice(0,50).map(item => [escapeHTML(item.descricao_pms || item.nome_interno),number(item.quantidade),`<strong>${money(item.total_a_receber)}</strong>`]))}</article>
    <article class="panel wide-panel"><div class="panel-heading"><div><div class="panel-title">Descontos documentados</div><div class="panel-subtitle">Regras extraídas da aba Desconto de Serviços</div></div></div>${table(['Serviço','Base','Desconto'],data.descontos.map(item=>[escapeHTML(item.descricao_pms),money(item.valor_receber),`<span class="money-negative">${money(item.valor_desconto)}</span>`]))}</article></section>`;
}

function proposalDialog() {
  const today = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Gerar proposta de medição</h2><p class="lede">Os lançamentos do período serão agrupados por serviço, bloco e casa. Colaboradores e observações não entram no documento.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="proposalForm"><div class="form-grid"><div class="field"><label>Data da proposta *</label><input name="data_proposta" type="date" value="${today}" required></div><div class="field"><label>PMS</label><input name="pms_numero" type="number" min="1" value="${escapeHTML(state.bootstrap.obra.pms_atual||'')}"></div><div class="field"><label>Início do período *</label><input name="periodo_inicio" type="date" value="${escapeHTML(state.start)}" required></div><div class="field"><label>Fim do período *</label><input name="periodo_fim" type="date" value="${escapeHTML(state.end)}" required></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Carregar proposta</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelector('#proposalForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;const button=form.querySelector('.primary-button');button.disabled=true;button.textContent='Gerando…';
    try{const created=await api('/api/proposals/generate',{method:'POST',body:JSON.stringify(formPayload(form))});state.rows.proposalId=created.id;dialog.close();toast('Proposta gerada a partir dos lançamentos.');await render('proposals');}
    catch(error){dialog.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;button.disabled=false;button.textContent='Tentar novamente';}
  });
}

function proposalGrid(proposal) {
  const rows=[];
  proposal.servicos.forEach(service=>{
    const items=service.itens.length?service.itens:[{id:null,bloco:'',casa:''}];
    let groupStart=0;
    while(groupStart<items.length){
      let groupEnd=groupStart;
      while(groupEnd+1<items.length&&(items[groupEnd+1].bloco||'')===(items[groupStart].bloco||''))groupEnd++;
      for(let index=groupStart;index<=groupEnd;index++){
        const item=items[index];let cells='';
        if(index===0)cells+=`<td class="proposal-service" rowspan="${items.length}"><input data-proposal-service="${service.id}" value="${escapeHTML(service.nome_servico)}"><span class="proposal-order"><button class="text-button" data-proposal-move="up" data-proposal-service-id="${service.id}" aria-label="Mover serviço para cima">↑</button><button class="text-button" data-proposal-move="down" data-proposal-service-id="${service.id}" aria-label="Mover serviço para baixo">↓</button><button class="text-button danger" data-proposal-service-delete="${service.id}">Excluir serviço</button></span></td>`;
        if(index===groupStart)cells+=`<td rowspan="${groupEnd-groupStart+1}"><input data-proposal-item-block data-proposal-block-item-ids="${items.slice(groupStart,groupEnd+1).map(entry=>entry.id).filter(Boolean).join(',')}" value="${escapeHTML(items[groupStart].bloco||'')}"></td>`;
        cells+=`<td><input data-proposal-item-house="${item.id||''}" value="${escapeHTML(item.casa||'')}"></td><td></td><td>${item.id?`<button class="text-button danger" data-proposal-item-delete="${item.id}">Remover</button>`:''}</td>`;
        rows.push(`<tr>${cells}</tr>`);
      }
      groupStart=groupEnd+1;
    }
    rows.push(`<tr class="proposal-add-row"><td colspan="5"><button class="secondary-button compact" data-proposal-add-item="${service.id}">＋ Adicionar casa/local</button></td></tr>`);
  });
  return `<div class="table-wrap proposal-grid"><table class="data-table"><thead><tr><th>Serviço</th><th>Bloco</th><th>Casa</th><th>Observações</th><th></th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

async function renderProposals() {
  const proposals=await api(withPeriod(`/api/proposals?obra_id=${state.obraId}`));
  const selected=state.rows.proposalId || proposals[0]?.id;
  const proposal=selected ? await api(`/api/proposals/${selected}`) : null;
  state.rows.proposalId=proposal?.id||null; state.rows.proposals=proposals; state.rows.proposal=proposal;
  const actions=can('pms')?'<button class="primary-button" data-action="new-proposal">＋ Gerar proposta</button>':'';
  const proposalArea=proposal?`<div class="proposal-workspace">
    <div class="proposal-toolbar">
      <label class="field"><span class="workspace-label">PROPOSTA</span><select id="proposalSelect">${proposals.map(item=>`<option value="${item.id}" ${item.id===proposal.id?'selected':''}>#${item.id} · ${dateBR(item.data_proposta)} · ${dateBR(item.periodo_inicio)} a ${dateBR(item.periodo_fim)}</option>`).join('')}</select></label>
      <div class="proposal-actions"><a class="secondary-button" href="/api/proposals/${proposal.id}.xlsx">Baixar Excel</a><a class="secondary-button" href="/api/proposals/${proposal.id}.pdf">Baixar PDF</a><button class="secondary-button" data-proposal-add-service>＋ Serviço</button></div>
    </div>
    <div class="proposal-meta"><span><strong>${escapeHTML(proposal.obra)}</strong><span>Empresa · ${escapeHTML(proposal.empresa)}</span></span><span><strong>${number(proposal.servicos.length)} serviços</strong><span>${number(proposal.servicos.reduce((total,service)=>total+service.itens.length,0))} locais · Data ${dateBR(proposal.data_proposta)}</span></span><span>Observações seguem vazias para conferência.</span></div>
    ${proposalGrid(proposal)}
  </div>`:'<div class="empty-note">Nenhuma proposta criada para esta obra. Gere uma proposta usando o período selecionado.</div>';
  app.innerHTML=`<section class="page proposal-page">${heading('Proposta de Medição','Grade editável por serviço, bloco e casa. A proposta não altera a medição PMS nem pagamentos.',actions)}${proposalArea}</section>`;
}

async function renderPeople() {
  const rows = await api(withPeriod(`/api/employees?obra_id=${state.obraId}`));
  state.rows.employees = rows;
  const action = state.currentUser.role==='admin' ? '<button class="primary-button" data-action="new-employee">＋ Novo funcionário</button>' : '';
  app.innerHTML = `<section class="page">${heading('Funcionários', `${number(rows.length)} cadastros centrais desta obra. O apontamento de campo usa cadastros próprios, vinculados em Configurações > Integração do campo.`, action)}<article class="panel">${table(['Nome','Profissão','Situação','Lançamentos','Valor calculado','Ações'], rows.map(item=>[escapeHTML(item.nome),escapeHTML(item.profissao||'—'),status(item.ativo===0?'Inativo':item.situacao),number(item.lancamentos),money(item.total_pago),state.currentUser.perfil==='admin'?`<div class="button-row"><button class="secondary-button compact" data-employee-edit="${item.id}">Editar</button><button class="text-button" data-history-entity="funcionarios" data-history-id="${item.id}" data-history-title="${escapeHTML(item.nome)}">Histórico</button>${item.ativo!==0?`<button class="text-button danger" data-deactivate="employees" data-id="${item.id}">Inativar</button>`:''}</div>`:'—']))}</article></section>`;
}

async function renderServices() {
  const rows = await api(withPeriod(`/api/services?obra_id=${state.obraId}`));
  state.rows.services = rows;
  const action = state.currentUser.role==='admin' ? '<button class="primary-button" data-action="new-service">＋ Novo serviço</button>' : '';
  app.innerHTML = `<section class="page">${heading('Serviços e preços', `${number(rows.length)} itens no catálogo central desta obra. Os serviços do campo são vinculados em Configurações > Integração do campo.`, action)}<article class="panel">${table(['Serviço','Unidade','Preço atual','Valor previsto a receber','Uso','Status','Ações'], rows.map(item=>[escapeHTML(item.nome_interno),escapeHTML(item.unidade||'—'),money(item.preco_pagamento_atual??item.preco_pagamento),money(item.valor_receber_atual??item.valor_receber_unitario),number(item.lancamentos),status(item.ativo===0?'Inativo':'Ativo'),state.currentUser.perfil==='admin'?`<div class="button-row"><button class="secondary-button compact" data-service-edit="${item.id}">Editar</button><button class="secondary-button compact" data-price-history="${item.id}">Preços</button><button class="secondary-button compact" data-employee-price="${item.id}">Preço individual</button>${item.ativo!==0?`<button class="text-button danger" data-deactivate="services" data-id="${item.id}">Inativar</button>`:''}</div>`:'—']))}</article></section>`;
}

async function renderReports() {
  const stats = await api(withPeriod(`/api/dashboard?obra_id=${state.obraId}`));
  app.innerHTML = `<section class="page">${heading('Relatórios', 'Documentos PDF, Excel e impressão seguindo o padrão da planilha PMS.')}
    <div class="stat-grid"><article class="stat-card"><span class="stat-label">Custo calculado</span><div class="stat-value">${money(stats.total_pagar)}</div></article><article class="stat-card"><span class="stat-label">Previsto a receber</span><div class="stat-value">${money(stats.total_receber)}</div></article><article class="stat-card"><span class="stat-label">Margem estimada</span><div class="stat-value">${money(stats.margem)}</div></article><article class="stat-card"><span class="stat-label">Lançamentos no período</span><div class="stat-value">${number(stats.total_lancamentos)}</div></article></div>
    <div class="report-grid">${['dados','resumo','pms','apropriacao','pagamento','reembolso'].map(type=>`<article class="panel report-card"><div><div class="panel-title">${escapeHTML(({dados:'Lançamentos',resumo:'Resumo',pms:'Medição PMS',apropriacao:'Apropriações',pagamento:'Valores a pagar',reembolso:'Reembolsos'})[type])}</div><div class="panel-subtitle">${dateBR(state.start)} a ${dateBR(state.end)}</div></div><div class="button-row"><a class="secondary-button compact" href="${withPeriod(`/api/reports/${type}.pdf?obra_id=${state.obraId}`)}">PDF</a><a class="secondary-button compact" href="${withPeriod(`/api/reports/${type}.xlsx?obra_id=${state.obraId}`)}">Excel</a><a class="secondary-button compact" target="_blank" href="${withPeriod(`/api/reports/${type}.html?obra_id=${state.obraId}`)}">Imprimir</a></div></article>`).join('')}</div></section>`;
}

async function renderUsers() {
  const rows = await api('/api/users');
  state.rows.users = rows;
  app.innerHTML = `<section class="page">${heading('Usuários do sistema', 'Gerencie administradores, usuários operacionais e acessos ativos.', '<button class="primary-button" data-action="new-user">＋ Novo usuário</button>')}
  <article class="panel">${table(['Nome','Usuário','Perfil','Status','Último acesso','Ações'], rows.map(item=>[
    escapeHTML(item.nome),`<code>${escapeHTML(item.username)}</code>`,status(profileNames[item.perfil]||item.perfil),status(item.ativo?'Ativo':'Desativado'),item.ultimo_login?dateBR(item.ultimo_login.slice(0,10)):'Nunca',`<div class="button-row"><button class="secondary-button compact" data-user-edit="${item.id}">Editar</button><button class="secondary-button compact" data-user-reset="${item.id}" data-user-name="${escapeHTML(item.nome)}">Redefinir senha</button><button class="text-button" data-history-entity="usuario" data-history-id="${item.id}" data-history-title="${escapeHTML(item.nome)}">Histórico</button></div>`
  ]))}</article></section>`;
}

async function renderSettings() {
  const data = await api(`/api/settings?obra_id=${state.obraId}`);
  state.rows.settings = data;
  state.pageSize = Number(data.preferences.linhas_por_pagina)||25;
  const admin = state.currentUser.perfil === 'admin';
  const items = [
    ['account','Minha conta','♙',true],
    ['company','Empresa','▤',admin],
    ['work','Obra','⌂',admin],
    ['access','Usuários e acessos','♧',admin],
    ['field','Integração do campo','⌖',can('lancamentos')],
    ['sync','Sincronização','↻',can('lancamentos')],
    ['data','Dados e backup','▣',admin],
    ['audit','Auditoria','≡',admin],
  ].filter(item=>item[3]);
  if (!items.some(item=>item[0]===state.settingsSection)) state.settingsSection=items[0]?.[0]||'account';
  let content='';
  if (state.settingsSection==='company') {
    const c=data.company;
    const logo=c.logo_base64?`<img class="settings-logo-preview" alt="Marca da empresa" src="data:${escapeHTML(c.logo_mime)};base64,${c.logo_base64}">`:'<div class="settings-logo-empty">Sem marca</div>';
    content=`<form id="settingsCompanyForm" class="settings-form"><div class="form-grid">
      <div class="field full"><label>Nome da empresa *</label><input name="nome" maxlength="180" required value="${escapeHTML(c.nome)}"></div>
      <div class="field"><label>CNPJ</label><input name="cnpj" inputmode="numeric" maxlength="18" value="${escapeHTML(c.cnpj)}"></div>
      <div class="field"><label>E-mail</label><input name="email" type="email" maxlength="180" value="${escapeHTML(c.email)}"></div>
      <div class="field"><label>Telefone</label><input name="telefone" maxlength="60" value="${escapeHTML(c.telefone)}"></div>
      <div class="field full"><label>Endereço</label><input name="endereco" maxlength="300" value="${escapeHTML(c.endereco)}"></div>
      <div class="field full"><label>Marca para relatórios (PNG ou JPG, até 2 MB)</label><div class="settings-logo-row">${logo}<div><input name="logo" type="file" accept="image/png,image/jpeg"><input name="remover_logo" type="hidden" value="false"><button class="text-button" type="button" data-action="settings-remove-logo" ${c.logo_base64?'':'disabled'}>Remover marca</button></div></div></div>
      <div class="field full"><small class="field-help">O nome e a marca aparecem nos relatórios emitidos pelo sistema e no apontamento de campo.</small></div>
    </div><div class="modal-footer"><button class="primary-button" type="submit">Salvar dados da empresa</button></div></form>`;
  } else if (state.settingsSection==='work') {
    const w=data.work;
    content=`<div class="settings-card-grid"><article class="panel"><div class="panel-heading"><div><div class="panel-title">Dados cadastrais</div><div class="panel-subtitle">${escapeHTML(w.nome)} · ${escapeHTML(w.cliente_contratante||'Contratante não informada')}</div></div><button class="secondary-button compact" data-action="edit-work">Editar obra</button></div><div class="settings-facts"><div><small>Código</small><strong>${escapeHTML(w.codigo||'—')}</strong></div><div><small>Status</small><strong>${escapeHTML(readableName(w.status, statusNames))}</strong></div><div><small>Engenheiro</small><strong>${escapeHTML(w.engenheiro_responsavel||'—')}</strong></div><div><small>Encarregado</small><strong>${escapeHTML(w.encarregado||'—')}</strong></div><div class="full"><small>Endereço</small><strong>${escapeHTML(w.endereco||'—')}</strong></div></div></article>
      <form id="settingsWorkForm" class="panel settings-form"><div class="panel-title">Regras operacionais do apontamento</div><p class="panel-subtitle">Aplicadas no sistema O gestor de Campo e sincronizadas com a área de apontamento.</p><div class="form-grid settings-work-grid">
        <div class="field full"><label>Responsável pelo apontamento</label><input name="responsavel_apontamento" maxlength="180" value="${escapeHTML(w.responsavel_apontamento||'')}"></div>
        <label class="settings-check field full"><input name="bloquear_datas_futuras" type="checkbox" ${w.bloquear_datas_futuras?'checked':''}><span><strong>Bloquear datas futuras</strong><small>O servidor também impede a gravação de apontamentos futuros.</small></span></label>
        <div class="field full"><span class="settings-fact-label">Quantidade em apontamento coletivo</span><output class="settings-readonly-value">Dividir igualmente entre os funcionários</output><small class="field-help">A soma das quantidades individuais preserva o total do apontamento de campo. Não cria medição nem pagamento.</small></div>
      </div><div class="modal-footer"><button class="primary-button">Salvar regras da obra</button></div></form></div>`;
  } else if (state.settingsSection==='account') {
    const pref=data.preferences;
    content=`<div class="settings-card-grid"><form id="settingsAccountForm" class="panel settings-form"><div class="panel-title">Dados pessoais</div><div class="form-grid settings-work-grid"><div class="field"><label>Nome *</label><input name="nome" maxlength="180" required value="${escapeHTML(state.currentUser.nome)}"></div><div class="field"><label>E-mail</label><input name="email" type="email" maxlength="180" value="${escapeHTML(state.currentUser.email||'')}"></div></div><div class="modal-footer"><button class="primary-button">Salvar meus dados</button></div></form>
      <form id="settingsPreferencesForm" class="panel settings-form"><div class="panel-title">Preferências pessoais</div><div class="form-grid settings-work-grid"><div class="field"><label>Obra inicial</label><select name="obra_padrao_id"><option value="">Manter a última obra</option>${data.works.map(w=>`<option value="${w.id}" ${Number(pref.obra_padrao_id)===Number(w.id)?'selected':''}>${escapeHTML(w.nome)}</option>`).join('')}</select></div><div class="field"><label>Linhas por página</label><select name="linhas_por_pagina">${[25,50,100].map(n=>`<option value="${n}" ${Number(pref.linhas_por_pagina)===n?'selected':''}>${n}</option>`).join('')}</select></div><div class="field full"><label>Tela inicial</label><select name="tela_inicial">${[['overview','Visão geral'],['entries','Lançamentos'],['quality','Qualidade'],['payments','Fechamento']].filter(([v])=>v==='overview'||can(({entries:'lancamentos',quality:'qualidade',payments:'pagamentos'})[v])).map(([v,l])=>`<option value="${v}" ${(['pms','forecast'].includes(pref.tela_inicial)?'payments':pref.tela_inicial)===v?'selected':''}>${l}</option>`).join('')}</select></div></div><div class="modal-footer"><button class="primary-button">Salvar preferências</button></div></form>
      <article class="panel"><div class="panel-heading"><div><div class="panel-title">Segurança da conta</div><div class="panel-subtitle">Sessões abertas em dispositivos autorizados.</div></div><button class="secondary-button compact" data-action="open-password">Alterar senha</button></div>${table(['Dispositivo / IP','Criada','Expira',''],data.sessions.map(s=>[escapeHTML(s.ip||'IP não informado'),escapeHTML(dateTimeBR(s.criado_em)),escapeHTML(dateTimeBR(s.expira_em)),s.atual?'<span class="pill">Esta sessão</span>':'']))}<div class="modal-footer"><button class="text-button" data-action="logout-other-sessions">Encerrar outras sessões</button></div></article>
      <article class="panel"><div class="panel-title">Seu perfil: ${escapeHTML(profileNames[state.currentUser.perfil]||state.currentUser.perfil)}</div><div class="panel-subtitle">Permissões e acesso são definidos pelo administrador.</div><ul class="settings-action-list">${(data.profiles[state.currentUser.perfil]||[]).map(action=>`<li>${escapeHTML(action)}</li>`).join('')}</ul></article></div>`;
  } else if (state.settingsSection==='access') {
    const rows=await api('/api/users'); state.rows.users=rows;
    content=`<div class="panel-heading settings-panel-heading"><div><div class="panel-title">Usuários do sistema</div><div class="panel-subtitle">Perfis, obras autorizadas, estado de acesso e redefinição de senha.</div></div><button class="primary-button" data-action="new-user">＋ Novo usuário</button></div><article class="panel">${table(['Nome','Usuário','Perfil','Obras','Status','Último acesso','Ações'],rows.map(item=>[
      escapeHTML(item.nome),`<code>${escapeHTML(item.username)}</code>`,status(profileNames[item.perfil]||item.perfil),escapeHTML(item.perfil==='admin'?'Todas':(item.obra_ids||[]).map(id=>data.works.find(w=>Number(w.id)===Number(id))?.nome||`#${id}`).join(', ')),status(item.ativo?'Ativo':'Desativado'),item.ultimo_login?escapeHTML(dateTimeBR(item.ultimo_login)):'Nunca',
      `<div class="button-row"><button class="secondary-button compact" data-user-edit="${item.id}">Editar</button><button class="secondary-button compact" data-user-reset="${item.id}" data-user-name="${escapeHTML(item.nome)}">Redefinir senha</button><button class="text-button" data-user-toggle="${item.id}">${item.ativo?'Desativar':'Ativar'}</button><button class="text-button" data-history-entity="usuario" data-history-id="${item.id}" data-history-title="${escapeHTML(item.nome)}">Histórico</button></div>`
    ]))}</article><article class="panel wide-panel"><div class="panel-title">Matriz de perfis</div><div class="settings-profile-grid">${Object.entries(data.profiles).map(([profile,actions])=>`<div><strong>${escapeHTML(profileNames[profile]||profile)}</strong><small>${actions.map(escapeHTML).join(' · ')}</small></div>`).join('')}</div></article>`;
  } else if (state.settingsSection==='field') {
    const [links,appointments]=await Promise.all([api(`/api/settings/field-links?obra_id=${state.obraId}`),api(`/api/settings/field-appointments?obra_id=${state.obraId}`)]);
    state.rows.fieldLinks=links; state.rows.fieldAppointments=appointments;
    const linkedEmployees=new Set((links.employees||[]).filter(row=>row.destino_id).map(row=>String(row.id)));
    const linkedServices=new Set((links.services||[]).filter(row=>row.destino_id).map(row=>String(row.id)));
    appointments.forEach(item=>{item.mapeamentoPendente=!linkedServices.has(String(item.servico_id))||item.funcionario_ids.some(id=>!linkedEmployees.has(String(id)));});
    const linkTable=(kind,label)=>table([`Cadastro no campo`,`Correspondência no sistema`,`Vínculo`],(links[kind]||[]).map(row=>[
      escapeHTML(row.nome),`<select class="filter-select" data-field-destination="${escapeHTML(kind)}:${escapeHTML(row.id)}"><option value="">Escolher ${label.toLowerCase()}</option>${row.opcoes.map(option=>`<option value="${option.id}" ${Number(row.destino_id)===Number(option.id)?'selected':''}>${escapeHTML(option.nome)}</option>`).join('')}</select>`,
      `<button class="secondary-button compact" data-field-link-save="${escapeHTML(kind)}:${escapeHTML(row.id)}">Salvar vínculo</button>`
    ]),'Cadastros do campo ainda não sincronizados.');
    content=`<div class="panel"><div class="panel-heading"><div><div class="panel-title">Vincular cadastros do campo</div><div class="panel-subtitle">Os cadastros do aplicativo de campo e os cadastros centrais são separados. Correspondências exatas são sugeridas automaticamente; confira antes de importar.</div></div></div><h3 class="settings-subheading">Funcionários necessários para importação · ${number(links.employees.length)}</h3>${linkTable('employees','funcionário')}<h3 class="settings-subheading">Serviços necessários para importação · ${number(links.services.length)}</h3>${linkTable('services','serviço')}</div>
      <article class="panel wide-panel"><div class="panel-heading"><div><div class="panel-title">Importar apontamentos finalizados</div><div class="panel-subtitle">Prévia antes da gravação. O total coletivo é dividido igualmente, com o restante atribuído ao último funcionário para preservar a soma.</div></div></div>${table(['Data','Colaboradores','Serviço','Local','Quantidade','Situação','Ação'],appointments.map(item=>[
        dateBR(item.data),escapeHTML(item.funcionarios.join(', ')),escapeHTML(item.servico),escapeHTML(item.local||'—'),`${number(item.quantidade)} ${escapeHTML(item.unidade||'')}`,item.importados?status(`${item.importados} lançado(s)`):'<span class="pill">Pronto para importar</span>',
        item.importados?'—':item.mapeamentoPendente?'<button class="secondary-button compact" disabled title="Vincule o serviço e todos os colaboradores aos cadastros centrais antes de importar">Vincule os cadastros</button>':`<button class="primary-button compact" data-field-import="${escapeHTML(item.id)}">Importar para produção</button>`
      ]),'Nenhum apontamento finalizado aguardando importação.')}</article>`;
  } else if (state.settingsSection==='sync') {
    const byKind=Object.fromEntries(data.field_status.map(row=>[row.kind,row]));
    content=`<div class="settings-card-grid"><article class="panel"><div class="panel-title">Sincronização do aplicativo de campo</div><p class="panel-subtitle">O servidor guarda os dados sincronizados desta obra. Registros offline permanecem no aparelho até a conexão voltar.</p>${table(['Dados do campo','Registros no servidor','Última atualização recebida'],data.pwa.stores.map(kind=>{const row=byKind[kind];return [escapeHTML(({employees:'Funcionários',services:'Serviços',locations:'Locais',appointments:'Apontamentos',settings:'Configurações',audit:'Auditoria'})[kind]||kind),number(row?.total||0),escapeHTML(dateTimeBR(row?.ultima_sincronizacao))]}))}</article><article class="panel"><div class="panel-title">Integração com o campo</div><p class="panel-subtitle">Este gestor recebe os apontamentos enviados pelo aplicativo separado e permite conferi-los antes de importar para produção.</p><button class="secondary-button" data-action="refresh-settings">Atualizar status</button><p class="field-help">A fila que ainda está somente no aparelho é indicada pelo próprio aplicativo de campo.</p></article></div>`;
  } else if (state.settingsSection==='data') {
    content=`<div class="settings-card-grid"><article class="panel"><div class="panel-title">Backup completo do banco</div><p class="panel-subtitle">Baixa uma cópia SQLite consistente de obras, cadastros, produção, usuários, configurações e auditoria.</p><a class="primary-button" href="/api/settings/backup">Baixar backup do sistema</a></article><article class="panel"><div class="panel-title">Restaurar banco</div><p class="panel-subtitle">A restauração substitui todos os dados atuais pelo arquivo selecionado. Um backup automático será guardado antes da troca.</p><form id="settingsRestoreForm" class="settings-form"><div class="form-grid settings-work-grid"><div class="field full"><label>Arquivo de backup do sistema</label><input name="backup" type="file" accept=".sqlite,.sqlite3,application/vnd.sqlite3" required></div><div class="field full"><label>Confirmação de segurança</label><input name="confirmacao" autocomplete="off" placeholder="Digite RESTAURAR" required></div></div><div class="modal-footer"><button class="danger-button">Restaurar banco</button></div></form></article></div><article class="panel wide-panel"><div class="panel-title">Importação de apontamentos</div><p class="panel-subtitle">A prévia e os vínculos dos cadastros ficam em “Integração do campo”. Confira os dados antes de importar para produção.</p><button class="secondary-button" data-settings-section="field">Abrir prévia e vínculos</button></article>`;
  } else if (state.settingsSection==='audit') {
    const filters=state.settingsAuditFilters;
    const query=new URLSearchParams({page:String(state.settingsAuditPage),limit:'25'});
    if(state.start)query.set('start',state.start);if(state.end)query.set('end',state.end);
    for(const key of ['entidade','acao','usuario','q'])if(filters[key])query.set(key,filters[key]);
    const auditData=await api(`/api/settings/audit?${query}`);state.rows.settingsAudit=auditData;
    content=`<form id="settingsAuditForm" class="panel settings-audit-filters"><div class="field"><label>Entidade</label><input name="entidade" value="${escapeHTML(filters.entidade||'')}" placeholder="Ex.: lancamento"></div><div class="field"><label>Ação</label><input name="acao" value="${escapeHTML(filters.acao||'')}" placeholder="Ex.: alterado"></div><div class="field"><label>Usuário</label><input name="usuario" value="${escapeHTML(filters.usuario||'')}"></div><div class="field"><label>Pesquisa</label><input name="q" value="${escapeHTML(filters.q||'')}" placeholder="Texto nos detalhes"></div><button class="primary-button">Filtrar auditoria</button></form><article class="panel">${table(['Data/hora','Usuário','Entidade','ID','Ação','Antes','Depois'],auditData.items.map(item=>[
      escapeHTML(dateTimeBR(item.criado_em)),escapeHTML(item.usuario||'Sistema'),escapeHTML(readableName(item.entidade,auditEntityNames)),escapeHTML(item.entidade_id||'—'),status(readableName(item.acao,auditActionNames)),`<code class="audit-json">${escapeHTML(item.antes||'—')}</code>`,`<code class="audit-json">${escapeHTML(item.depois||item.detalhes||'—')}</code>`
    ]))}<div class="settings-pagination"><span>${number(auditData.total)} eventos · página ${auditData.page} de ${auditData.pages||1}</span><div class="button-row"><button class="secondary-button compact" data-audit-page="${Math.max(1,auditData.page-1)}" ${auditData.page<=1?'disabled':''}>Anterior</button><button class="secondary-button compact" data-audit-page="${Math.min(auditData.pages||1,auditData.page+1)}" ${auditData.page>=(auditData.pages||1)?'disabled':''}>Próxima</button></div></div></article>`;
  }
  app.innerHTML=`<section class="page">${heading('Configurações','Empresa, obra, acesso, conta, dados e integrações em um só lugar.')}<div class="settings-layout"><div class="settings-mobile-nav"><label for="settingsSectionSelect">Seção das configurações</label><select id="settingsSectionSelect" aria-label="Seção das configurações">${items.map(([key,label])=>`<option value="${key}" ${state.settingsSection===key?'selected':''}>${escapeHTML(label)}</option>`).join('')}</select></div><nav class="settings-tabs" aria-label="Seções das configurações">${items.map(([key,label,icon])=>`<button class="settings-tab ${state.settingsSection===key?'active':''}" data-settings-section="${key}"><span>${icon}</span>${label}</button>`).join('')}</nav><div class="settings-body">${content}</div></div></section>`;
}

const renderers = { overview: renderOverview, entries: renderEntries, appropriations: renderAppropriations, quality: renderQuality, payments: renderPayments, pms: renderPMS, proposals: renderProposals, forecast: renderForecast, summary: renderSummary, reimbursements: renderReimbursements, people: renderPeople, services: renderServices, reports: renderReports, users: renderUsers, audit: renderAudit, settings: renderSettings };

async function render(view = state.view) {
  if (view === 'pms' || view === 'forecast') view = 'payments';
  state.view = view;
  loading();
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  try { await renderers[view](); bindViewEvents(); }
  catch (error) { app.innerHTML = `<div class="error-state"><div><strong>Não foi possível carregar esta área.</strong>${escapeHTML(error.message)}<br><button class="secondary-button" data-action="retry">Tentar novamente</button></div></div>`; bindViewEvents(); }
}

function entryDialog() {
  if (!can('lancamentos')) { toast('Seu perfil não pode criar lançamentos.', 'error'); return; }
  const people = activeEntryEmployees();
  const services = activeEntryServices();
  if (!people.length || !services.length) {
    toast(!people.length ? 'Não há colaborador ativo nesta obra.' : 'Não há serviço ativo nesta obra.', 'error');
    return;
  }
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Novo lançamento</h2><p class="lede">O pagamento e o valor PMS serão calculados ao salvar.</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="entryForm"><div class="form-grid">
    <div class="field"><label for="entryDate">Data *</label><input id="entryDate" name="data" type="date" required aria-required="true"><small class="field-help"></small></div>
    <div class="field"><label for="entryType">Tipo *</label><select id="entryType" name="tipo" required><option value="produção">Produção</option><option value="diaria">Diária</option><option value="apropriado">Apropriado</option></select><small class="field-help"></small></div>
    <div class="field"><label for="employee">Funcionário *</label><select id="employee" name="funcionario_id" required>${people.map(x=>`<option value="${x.id}">${escapeHTML(x.nome)} · ${escapeHTML(x.profissao||'—')}</option>`).join('')}</select><small class="field-help"></small></div>
    <div class="field"><label for="service">Serviço *</label><select id="service" name="servico_id" required>${services.map(x=>`<option value="${x.id}">${escapeHTML(x.nome_interno)}</option>`).join('')}</select><small class="field-help"></small></div>
    <div class="field"><label for="quantity">Quantidade / m²</label><input id="quantity" name="quantidade_m2" type="number" min="0" step="0.0001" placeholder="0,0000"><small class="field-help">Tem prioridade sobre horas.</small></div>
    <div class="field"><label for="hours">Horas / diárias</label><input id="hours" name="horas_trabalhadas" type="number" min="0" step="0.01" placeholder="0,00"><small class="field-help">Não gera valor a receber.</small></div>
    <div class="field"><label for="extra">Extra</label><input id="extra" name="extra" type="number" min="0" step="0.01" placeholder="0,00"><small class="field-help"></small></div>
    <div class="field"><label for="discount">Desconto</label><input id="discount" name="desconto" type="number" min="0" step="0.01" placeholder="0,00"><small class="field-help"></small></div>
    <div class="field"><label for="house">Casa</label><input id="house" name="casa" placeholder="Ex.: 224"><small class="field-help"></small></div>
    <div class="field"><label for="block">Bloco</label><input id="block" name="bloco" placeholder="Ex.: 12"><small class="field-help"></small></div>
    <div class="field"><label for="apartment">AP</label><input id="apartment" name="ap" placeholder="Ex.: 204"><small class="field-help"></small></div>
    <div class="field full"><label for="notes">Observação</label><textarea id="notes" name="obs" placeholder="Informações para conferência"></textarea><small class="field-help"></small></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button" type="submit">Calcular e salvar</button></div></form></div>`;
  dialog.showModal();
  document.querySelector('#entryDate').value = state.end || new Date().toISOString().slice(0, 10);
  bindDialog('entryForm', '/api/entries', 'Lançamento salvo e valores calculados.');
}

function appropriationDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Nova apropriação</h2><p class="lede">Sextas terminam às 16:00; nos demais dias, às 17:00.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="appropriationForm"><div class="form-grid">
    <div class="field"><label>Data *</label><input name="data" type="date" required></div><div class="field"><label>PMS</label><input name="pms_numero" type="number" value="${state.bootstrap.obra.pms_atual||''}"></div>
    <div class="field"><label>Funcionário *</label><select name="funcionario_id" required>${state.bootstrap.funcionarios.filter(x=>x.ativo!==0).map(x=>`<option value="${x.id}">${escapeHTML(x.nome)} · ${escapeHTML(x.profissao||'—')}</option>`).join('')}</select></div>
    <div class="field"><label>Serviço *</label><select name="servico_id" required>${state.bootstrap.servicos.filter(x=>x.ativo!==0).map(x=>`<option value="${x.id}">${escapeHTML(x.nome_interno)}</option>`).join('')}</select></div>
    <div class="field"><label>Início manhã</label><input name="inicio_manha" type="time" value="07:00"></div><div class="field"><label>Término manhã</label><input name="termino_manha" type="time" value="12:00"></div>
    <div class="field"><label>Início tarde</label><input name="inicio_tarde" type="time" value="13:00"></div><div class="field"><label>Término tarde</label><input name="termino_tarde" type="time"></div>
    <div class="field full"><label>Assinatura do encarregado</label><input name="assinatura_encarregado"></div><div class="field full"><label>Observação</label><textarea name="observacao"></textarea></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar apropriação</button></div></form></div>`;
  dialog.showModal(); dialog.querySelector('[name="data"]').value = state.end || new Date().toISOString().slice(0, 10);
  bindDialog('appropriationForm','/api/appropriations','Apropriação salva.');
}

function qualityDialog(button) {
  const previousNote = button.dataset.qualityStatus === 'pendente' ? button.dataset.qualityNote || '' : '';
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Avaliar problema</h2><p class="lede">Descreva o que precisa ser corrigido antes da aprovação.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="qualityForm"><input type="hidden" name="lancamento_id" value="${button.dataset.quality}"><input type="hidden" name="situacao" value="pendente"><div class="form-grid">
    <div class="field full"><label>Problema encontrado *</label><textarea name="observacao" required placeholder="Informe a não conformidade e a correção necessária">${escapeHTML(previousNote)}</textarea><small class="field-help">O registro ficará pendente e a avaliação será preservada no histórico.</small></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Registrar problema</button></div></form></div>`;
  dialog.showModal();
  bindDialog('qualityForm','/api/quality','Problema registrado na qualidade.');
}

async function quickQualityOK(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'Salvando…';
  try {
    await api('/api/quality', { method: 'POST', body: JSON.stringify({
      obra_id: state.obraId,
      lancamento_id: Number(button.dataset.qualityOk),
      situacao: 'ok',
      observacao: 'Aprovado pelo botão rápido de qualidade.',
    }) });
    toast('Qualidade aprovada e registrada no histórico.');
    await render('quality');
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    toast(error.message, 'error');
  }
}

function entryDeleteDialog(item) {
  dialog.innerHTML=`<div class="modal"><div class="modal-head"><div><h2>Excluir lançamento</h2><p class="lede">O lançamento será retirado das listas e cálculos ativos, mas seu histórico será preservado. Lançamentos medidos ou vinculados a fechamento não podem ser excluídos.</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="entryDeleteForm"><div class="field"><label>Motivo da exclusão *</label><textarea name="motivo" required maxlength="500" rows="3" placeholder="Ex.: lançamento duplicado"></textarea></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="danger-button">Excluir lançamento</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelector('#entryDeleteForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.currentTarget.querySelector('.danger-button');button.disabled=true;
    try{await api(`/api/entries/${item.id}`,{method:'DELETE',body:JSON.stringify(formPayload(event.currentTarget))});dialog.close();toast('Lançamento excluído da operação; histórico preservado.');await loadBootstrap();await render('entries');}
    catch(error){document.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;button.disabled=false;}
  });
}

function closingDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><h2>Gerar fechamento</h2><button class="close" data-close>×</button></div><div id="modalError"></div><form id="closingForm"><div class="form-grid">
    <div class="field"><label>Início *</label><input name="periodo_inicio" type="date" required></div><div class="field"><label>Fim *</label><input name="periodo_fim" type="date" required></div>
    <div class="field full"><label>PMS</label><input name="pms_numero" type="number" value="${state.bootstrap.obra.pms_atual||''}"></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Gerar fechamento</button></div></form></div>`;
  dialog.showModal(); dialog.querySelector('[name="periodo_inicio"]').value=state.start||state.bootstrap.periodo.inicio; dialog.querySelector('[name="periodo_fim"]').value=state.end||state.bootstrap.periodo.fim;
  bindDialog('closingForm','/api/closings','Fechamento gerado.');
}

function paymentProposalDialog() {
  dialog.innerHTML=`<div class="modal"><div class="modal-head"><div><h2>Gerar proposta de pagamento</h2><p class="lede">Agrupa a produção por funcionário, serviço e localização. É uma proposta de conferência, não uma quitação.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="paymentProposalForm"><div class="form-grid"><div class="field"><label>Início *</label><input name="periodo_inicio" type="date" value="${escapeHTML(state.start)}" required></div><div class="field"><label>Fim *</label><input name="periodo_fim" type="date" value="${escapeHTML(state.end)}" required></div><div class="field full"><label>PMS (opcional)</label><input name="pms_numero" type="number" min="1" value="${escapeHTML(state.bootstrap.obra.pms_atual||'')}"></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Gerar proposta</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelector('#paymentProposalForm').addEventListener('submit',async event=>{
    event.preventDefault(); const button=event.currentTarget.querySelector('button.primary-button'); button.disabled=true;
    try{const payload={...formPayload(event.currentTarget),obra_id:state.obraId};const created=await api('/api/payment-proposals/generate',{method:'POST',body:JSON.stringify(payload)});state.rows.paymentProposalId=created.id;dialog.close();toast('Proposta de pagamento gerada.');await render('payments');}
    catch(error){document.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;button.disabled=false;}
  });
}

function paymentProposalItemDialog() {
  const employees=state.bootstrap.funcionarios.filter(item=>item.ativo);
  const services=state.bootstrap.servicos.filter(item=>item.ativo);
  dialog.innerHTML=`<div class="modal payment-line-modal"><div class="modal-head"><div><h2>Adicionar linha à proposta</h2><p class="lede">Os valores desta linha são editáveis e ficam só na proposta.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="paymentProposalItemForm"><div class="form-grid"><div class="field full"><label>Funcionário *</label><select name="funcionario_id" required>${employees.map(item=>`<option value="${item.id}">${escapeHTML(item.nome)}</option>`).join('')}</select></div><div class="field full"><label>Serviço *</label><select name="servico_id" required>${services.map(item=>`<option value="${item.id}">${escapeHTML(item.nome_interno)}</option>`).join('')}</select></div><div class="field"><label>Bloco</label><input name="bloco" maxlength="80"></div><div class="field"><label>AP</label><input name="ap" maxlength="80"></div><div class="field"><label>Casa</label><input name="casa" maxlength="80"></div><div class="field"><label>Quantidade</label><input name="quantidade" type="number" min="0" step="0.0001" value="0"></div><div class="field"><label>Valor gerado</label><input name="valor_gerado" type="number" min="0" step="0.01" value="0"></div><div class="field"><label>Valor a pagar</label><input name="valor_pagar" type="number" min="0" step="0.01" value="0"></div><div class="field full"><label>Observação</label><textarea name="observacao" rows="2" maxlength="500"></textarea></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Adicionar</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelector('#paymentProposalItemForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.currentTarget.querySelector('button.primary-button');button.disabled=true;
    try{await api(`/api/payment-proposals/${state.rows.paymentProposal.id}/items`,{method:'POST',body:JSON.stringify(formPayload(event.currentTarget))});dialog.close();toast('Linha adicionada à proposta.');await render('payments');}
    catch(error){document.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;button.disabled=false;}
  });
}

function pmsDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><h2>Gerar medição PMS</h2><button class="close" data-close>×</button></div><div id="modalError"></div><form id="pmsForm"><div class="form-grid">
    <div class="field"><label>Número *</label><input name="numero" type="number" required value="${state.bootstrap.obra.pms_atual||''}"></div><div class="field"><label>Status</label><select name="status"><option>rascunho</option><option>em_conferencia</option><option>aprovado</option></select></div>
    <div class="field"><label>Início *</label><input name="periodo_inicio" type="date" required></div><div class="field"><label>Fim *</label><input name="periodo_fim" type="date" required></div><div class="field full"><label>Observação</label><textarea name="observacao"></textarea></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Gerar PMS</button></div></form></div>`;
  dialog.showModal(); dialog.querySelector('[name="periodo_inicio"]').value=state.start||state.bootstrap.periodo.inicio; dialog.querySelector('[name="periodo_fim"]').value=state.end||state.bootstrap.periodo.fim;
  bindDialog('pmsForm','/api/pms/generate','Medição PMS gerada.');
}

function reimbursementDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><h2>Novo reembolso</h2><button class="close" data-close>×</button></div><div id="modalError"></div><form id="reimbursementForm"><div class="form-grid">
    <div class="field"><label>Data *</label><input name="data" type="date" required></div><div class="field"><label>PMS</label><input name="pms_numero" type="number" value="${state.bootstrap.obra.pms_atual||''}"></div>
    <div class="field full"><label>Funcionário</label><select name="funcionario_id"><option value="">Sem vínculo</option>${state.bootstrap.funcionarios.map(x=>`<option value="${x.id}">${escapeHTML(x.nome)}</option>`).join('')}</select></div>
    <div class="field full"><label>Descrição *</label><textarea name="descricao" required></textarea></div><div class="field"><label>Valor</label><input name="valor" type="number" min="0" step="0.01"></div><div class="field"><label>Status</label><select name="status"><option>pendente</option><option>aprovado</option><option>reembolsado</option></select></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar reembolso</button></div></form></div>`;
  dialog.showModal(); dialog.querySelector('[name="data"]').value=state.end || new Date().toISOString().slice(0, 10);
  bindDialog('reimbursementForm','/api/reimbursements','Reembolso salvo.');
}

function appropriationEditDialog(item) {
  const controlled = item.status !== 'aberta';
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>${controlled?'Retificar':'Editar'} apropriação</h2><p class="lede">Valores atuais carregados do banco.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="appropriationEditForm"><input type="hidden" name="versao" value="${item.versao||1}"><div class="form-grid">
    <div class="field"><label>Data *</label><input name="data" type="date" required value="${escapeHTML(item.data)}"></div><div class="field"><label>PMS</label><input name="pms_numero" type="number" value="${item.pms_numero??''}"></div>
    <div class="field"><label>Colaborador *</label><select name="funcionario_id">${state.bootstrap.funcionarios.filter(x=>x.ativo!==0).map(x=>`<option value="${x.id}" ${x.id===item.funcionario_id?'selected':''}>${escapeHTML(x.nome)}</option>`).join('')}</select></div><div class="field"><label>Serviço *</label><select name="servico_id">${state.bootstrap.servicos.filter(x=>x.ativo!==0).map(x=>`<option value="${x.id}" ${x.id===item.servico_id?'selected':''}>${escapeHTML(x.nome_interno)}</option>`).join('')}</select></div>
    <div class="field"><label>Início manhã</label><input name="inicio_manha" type="time" value="${escapeHTML(item.inicio_manha||'')}"></div><div class="field"><label>Término manhã</label><input name="termino_manha" type="time" value="${escapeHTML(item.termino_manha||'')}"></div><div class="field"><label>Início tarde</label><input name="inicio_tarde" type="time" value="${escapeHTML(item.inicio_tarde||'')}"></div><div class="field"><label>Término tarde</label><input name="termino_tarde" type="time" value="${escapeHTML(item.termino_tarde||'')}"></div>
    <div class="field full"><label>Assinatura do encarregado</label><input name="assinatura_encarregado" value="${escapeHTML(item.assinatura_encarregado||'')}"></div><div class="field full"><label>Observação</label><textarea name="observacao">${escapeHTML(item.observacao||'')}</textarea></div>${controlled?'<div class="field full"><label>Motivo da retificação *</label><textarea name="motivo_alteracao" required></textarea></div>':''}
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar alterações</button></div></form></div>`;
  dialog.showModal(); bindDialog('appropriationEditForm',`/api/appropriations/${item.id}`,'Apropriação atualizada.','PUT');
}

function reimbursementEditDialog(item) {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Editar reembolso</h2><p class="lede">Alterações financeiras exigem motivo e ficam auditadas.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="reimbursementEditForm"><input type="hidden" name="versao" value="${item.versao||1}"><div class="form-grid">
    <div class="field"><label>Data *</label><input name="data" type="date" required value="${escapeHTML(item.data)}"></div><div class="field"><label>PMS</label><input name="pms_numero" type="number" value="${item.pms_numero??''}"></div>
    <div class="field full"><label>Colaborador</label><select name="funcionario_id"><option value="">Sem vínculo</option>${state.bootstrap.funcionarios.map(x=>`<option value="${x.id}" ${x.id===item.funcionario_id?'selected':''}>${escapeHTML(x.nome)}</option>`).join('')}</select></div>
    <div class="field full"><label>Descrição *</label><textarea name="descricao" required>${escapeHTML(item.descricao)}</textarea></div><div class="field"><label>Valor *</label><input name="valor" type="number" min="0" step="0.01" required value="${item.valor}"></div><div class="field"><label>Status</label><select name="status">${['pendente','aprovado','reembolsado','cancelado'].map(value=>`<option ${item.status===value?'selected':''}>${value}</option>`).join('')}</select></div>
    <div class="field full"><label>Motivo da alteração *</label><textarea name="motivo_alteracao" required></textarea></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Confirmar alteração</button></div></form></div>`;
  dialog.showModal(); bindDialog('reimbursementEditForm',`/api/reimbursements/${item.id}`,'Reembolso atualizado com auditoria.','PUT');
}

function forecastEditDialog(item) {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Editar previsão PMS</h2><p class="lede">O total será recalculado no servidor.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="forecastEditForm"><div class="form-grid"><div class="field full"><label>Descrição</label><input name="descricao_pms" value="${escapeHTML(item.descricao_pms||'')}"></div><div class="field"><label>Valor unitário</label><input name="valor_unitario" type="number" min="0" step="0.0001" value="${item.valor_unitario}"></div><div class="field"><label>Quantidade</label><input name="quantidade" type="number" min="0" step="0.0001" value="${item.quantidade}"></div><div class="field full"><label>Motivo da alteração *</label><textarea name="motivo_alteracao" required></textarea></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar alteração</button></div></form></div>`;
  dialog.showModal(); bindDialog('forecastEditForm',`/api/forecast/${item.id}`,'Previsão atualizada com auditoria.','PUT');
}

async function qualityHistoryDialog(launchId) {
  const rows = await api(`/api/quality/${launchId}/history`);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Histórico de qualidade</h2><p class="lede">Reprovações e aprovações anteriores são preservadas.</p></div><button class="close" data-close>×</button></div>${table(['Data','Situação','Observação','Técnico'],rows.map(item=>[escapeHTML((item.registrado_em||'').replace('T',' ').slice(0,19)),status(item.situacao),escapeHTML(item.observacao||'—'),escapeHTML(item.tecnico||'Sistema')]),'Nenhuma avaliação registrada.')}<div class="modal-footer"><button class="secondary-button" data-close>Fechar</button></div></div>`;
  dialog.showModal(); dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
}

function employeeDialog(item = null) {
  const editing = Boolean(item);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><h2>${editing?'Editar colaborador':'Novo colaborador'}</h2><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="employeeForm"><input type="hidden" name="versao" value="${item?.versao||1}"><div class="form-grid">
    <div class="field full"><label>Nome *</label><input name="nome" required value="${escapeHTML(item?.nome||'')}"></div>
    <div class="field"><label>Profissão</label><input name="profissao" value="${escapeHTML(item?.profissao||'')}"></div>
    <div class="field"><label>Situação</label><select name="situacao">${['efetivado','afastado','férias','desligado'].map(value=>`<option ${item?.situacao===value?'selected':''}>${value}</option>`).join('')}</select></div>
    <div class="field"><label>Telefone</label><input name="telefone" value="${escapeHTML(item?.telefone||'')}"></div>
    <div class="field"><label>CPF/documento</label><input name="documento" value="${escapeHTML(item?.documento||'')}"></div>
    <div class="field"><label>Data de admissão</label><input name="data_admissao" type="date" value="${escapeHTML(item?.data_admissao||'')}"></div>
    <div class="field"><label>Data de desligamento</label><input name="data_desligamento" type="date" value="${escapeHTML(item?.data_desligamento||'')}"></div>
    <div class="field"><label>Tipo de vínculo</label><select name="tipo_vinculo"><option value="">Não informado</option>${['CLT','PJ','Terceirizado','Prestador','Empreiteiro'].map(value=>`<option ${item?.tipo_vinculo===value?'selected':''}>${value}</option>`).join('')}</select></div>
    <div class="field full"><label>Observações</label><textarea name="observacoes">${escapeHTML(item?.observacoes||'')}</textarea></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">${editing?'Salvar alterações':'Salvar colaborador'}</button></div></form></div>`;
  dialog.showModal(); bindDialog('employeeForm',editing?`/api/employees/${item.id}`:'/api/employees',editing?'Colaborador atualizado.':'Colaborador cadastrado.',editing?'PUT':'POST');
}

function serviceDialog(item = null) {
  const editing = Boolean(item);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>${editing?'Editar serviço':'Novo serviço'}</h2><p class="lede">${editing?'Preços são alterados no histórico de vigências.':'Informe também o preço inicial.'}</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="serviceForm"><input type="hidden" name="versao" value="${item?.versao||1}"><div class="form-grid">
    <div class="field"><label>Código</label><input name="codigo" value="${escapeHTML(item?.codigo||'')}"></div>
    <div class="field"><label>Nome *</label><input name="nome_interno" required value="${escapeHTML(item?.nome_interno||'')}"></div>
    <div class="field full"><label>Descrição PMS</label><textarea name="descricao_pms">${escapeHTML(item?.descricao_pms||'')}</textarea></div>
    <div class="field"><label>Categoria</label><input name="categoria" value="${escapeHTML(item?.categoria||'')}"></div>
    <div class="field"><label>Unidade</label><input name="unidade" value="${escapeHTML(item?.unidade||'')}" placeholder="m², m ou unid"></div>
    <div class="field"><label>Quantidade padrão</label><input name="quantidade_padrao" type="number" min="0" step="0.0001" value="${item?.quantidade_padrao??1}"></div>
    ${editing?'':`<div class="field"><label>Preço inicial de pagamento</label><input name="preco_pagamento" type="number" min="0" step="0.0001" value="0"></div><div class="field"><label>Valor inicial a receber</label><input name="valor_receber_unitario" type="number" min="0" step="0.0001" value="0"></div><div class="field"><label>Início da vigência *</label><input name="inicio_vigencia" type="date" required value="${new Date().toISOString().slice(0,10)}"></div><div class="field full"><label>Motivo do preço inicial *</label><textarea name="motivo_preco" required>Preço inicial do serviço</textarea></div>`}
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">${editing?'Salvar alterações':'Salvar serviço'}</button></div></form></div>`;
  dialog.showModal(); bindDialog('serviceForm',editing?`/api/services/${item.id}`:'/api/services',editing?'Serviço atualizado.':'Serviço cadastrado.',editing?'PUT':'POST');
}

function entryEditDialog(item) {
  const locked = Boolean(item.pms_numero);
  const people = state.bootstrap.funcionarios.filter(x => x.ativo !== 0);
  const services = state.bootstrap.servicos.filter(x => x.ativo !== 0);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>${locked?'Retificar':'Editar'} apontamento</h2><p class="lede">${locked?'Registro medido: a alteração será auditada e não reescreverá o fechamento histórico.':'Os valores serão recalculados conforme o preço vigente na data.'}</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="entryEditForm"><input type="hidden" name="versao" value="${item.versao||1}">${locked?'<input type="hidden" name="retificar" value="true">':''}<div class="form-grid">
    <div class="field"><label>Data *</label><input name="data" type="date" required value="${escapeHTML(item.data)}"></div>
    <div class="field"><label>Tipo *</label><select name="tipo">${['produção','diaria','apropriado'].map(value=>`<option value="${value}" ${item.tipo===value?'selected':''}>${value}</option>`).join('')}</select></div>
    <div class="field"><label>Colaborador *</label><select name="funcionario_id">${people.map(x=>`<option value="${x.id}" ${x.id===item.funcionario_id?'selected':''}>${escapeHTML(x.nome)}</option>`).join('')}</select></div>
    <div class="field"><label>Serviço *</label><select name="servico_id">${services.map(x=>`<option value="${x.id}" ${x.id===item.servico_id?'selected':''}>${escapeHTML(x.nome_interno)}</option>`).join('')}</select></div>
    <div class="field"><label>Quantidade</label><input name="quantidade_m2" type="number" min="0" step="0.0001" value="${item.quantidade_m2??''}"></div>
    <div class="field"><label>Horas</label><input name="horas_trabalhadas" type="number" min="0" step="0.01" value="${item.horas_trabalhadas??''}"></div>
    <div class="field"><label>Extra</label><input name="extra" type="number" min="0" step="0.01" value="${item.extra??0}"></div>
    <div class="field"><label>Desconto</label><input name="desconto" type="number" min="0" step="0.01" value="${item.desconto??0}"></div>
    <div class="field"><label>Casa</label><input name="casa" value="${escapeHTML(item.casa||'')}"></div>
    <div class="field"><label>Bloco</label><input name="bloco" value="${escapeHTML(item.bloco||'')}"></div>
    <div class="field"><label>AP</label><input name="ap" value="${escapeHTML(item.ap||'')}"></div>
    <div class="field full"><label>Observação</label><textarea name="obs">${escapeHTML(item.obs||'')}</textarea></div>
    ${locked?'<div class="field full"><label>Motivo da retificação *</label><textarea name="motivo_alteracao" required></textarea><small class="field-help warning-text">Alteração crítica em registro já medido.</small></div>':''}
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">${locked?'Confirmar retificação':'Salvar alterações'}</button></div></form></div>`;
  dialog.showModal(); bindDialog('entryEditForm',`/api/entries/${item.id}`,locked?'Apontamento retificado com auditoria.':'Apontamento atualizado.', 'PUT');
}

function userDialog() {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Novo usuário</h2><p class="lede">A pessoa deverá trocar a senha no primeiro acesso.</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div><form id="userForm"><div class="form-grid"><div class="field full"><label>Nome *</label><input name="nome" required></div><div class="field"><label>Usuário *</label><input name="username" autocomplete="off" required></div><div class="field"><label>E-mail</label><input name="email" type="email"></div><div class="field"><label>Perfil *</label><select name="perfil"><option value="operador">Operador</option><option value="engenharia">Engenharia</option><option value="qualidade">Qualidade</option><option value="financeiro">Responsável por pagamentos</option><option value="admin">Administrador</option></select></div><div class="field full"><label>Senha temporária *</label><input name="password" type="password" autocomplete="new-password" required><small class="field-help">8 caracteres, maiúscula, minúscula, número e símbolo.</small></div><div class="field full"><label>Obras autorizadas</label><div class="checkbox-grid">${state.works.map(work=>`<label><input type="checkbox" name="obra_ids" value="${work.id}" ${work.id===state.obraId?'checked':''}> ${escapeHTML(work.nome)}</label>`).join('')}</div></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Criar usuário</button></div></form></div>`;
  dialog.showModal(); bindDialog('userForm','/api/users','Usuário criado.');
}

function userEditDialog(item) {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Editar usuário</h2><p class="lede">A senha permanece protegida e usa fluxo separado.</p></div><button class="close" data-close>×</button></div><div id="modalError"></div><form id="userEditForm"><input type="hidden" name="versao" value="${item.versao||1}"><div class="form-grid">
    <div class="field full"><label>Nome *</label><input name="nome" required value="${escapeHTML(item.nome)}"></div>
    <div class="field"><label>E-mail</label><input name="email" type="email" value="${escapeHTML(item.email||'')}"></div>
    <div class="field"><label>Perfil</label><select name="perfil">${Object.entries(profileNames).map(([value,label])=>`<option value="${value}" ${item.perfil===value?'selected':''}>${escapeHTML(label)}</option>`).join('')}</select></div>
    <div class="field"><label>Status</label><select name="ativo"><option value="1" ${item.ativo?'selected':''}>Ativo</option><option value="0" ${!item.ativo?'selected':''}>Inativo</option></select></div>
    <div class="field full"><label>Obras autorizadas</label><div class="checkbox-grid">${state.works.map(work=>`<label><input type="checkbox" name="obra_ids" value="${work.id}" ${(item.obra_ids||[]).includes(work.id)?'checked':''}> ${escapeHTML(work.nome)}</label>`).join('')}</div></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar alterações</button></div></form></div>`;
  dialog.showModal(); bindDialog('userEditForm',`/api/users/${item.id}`,'Usuário atualizado.','PUT');
}

function workEditDialog() {
  const item = state.bootstrap.obra;
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><h2>Editar obra</h2><button class="close" data-close>×</button></div><div id="modalError"></div><form id="workEditForm"><input type="hidden" name="versao" value="${item.versao||1}"><div class="form-grid">
    <div class="field"><label>Código</label><input name="codigo" value="${escapeHTML(item.codigo||'')}"></div><div class="field"><label>Nome *</label><input name="nome" required value="${escapeHTML(item.nome)}"></div>
    <div class="field"><label>Contratante</label><input name="cliente_contratante" value="${escapeHTML(item.cliente_contratante||'')}"></div><div class="field"><label>CNPJ</label><input name="cnpj_contratante" value="${escapeHTML(item.cnpj_contratante||'')}"></div>
    <div class="field full"><label>Endereço</label><input name="endereco" value="${escapeHTML(item.endereco||'')}"></div><div class="field"><label>Cidade</label><input name="cidade" value="${escapeHTML(item.cidade||'')}"></div><div class="field"><label>Estado</label><input name="estado" maxlength="2" value="${escapeHTML(item.estado||'')}"></div>
    <div class="field"><label>Data de início</label><input name="data_inicio" type="date" value="${escapeHTML(item.data_inicio||'')}"></div><div class="field"><label>Previsão de término</label><input name="previsao_termino" type="date" value="${escapeHTML(item.previsao_termino||'')}"></div>
    <div class="field"><label>Engenheiro</label><input name="engenheiro_responsavel" value="${escapeHTML(item.engenheiro_responsavel||'')}"></div><div class="field"><label>Encarregado</label><input name="encarregado" value="${escapeHTML(item.encarregado||'')}"></div>
    <div class="field"><label>Status</label><select name="status">${['planejamento','ativa','paralisada','finalizada','cancelada'].map(value=>`<option ${item.status===value?'selected':''}>${value}</option>`).join('')}</select></div><div class="field full"><label>Observações</label><textarea name="observacoes">${escapeHTML(item.observacoes||'')}</textarea></div>
  </div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar alterações</button></div></form></div>`;
  dialog.showModal(); bindDialog('workEditForm',`/api/works/${item.id}`,'Obra atualizada.','PUT');
}

async function priceHistoryDialog(serviceId) {
  const service = (state.rows.services||[]).find(item=>item.id===serviceId);
  const prices = await api(`/api/services/${serviceId}/prices`);
  state.rows.prices = prices;
  const latest = prices[0];
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Histórico de preços</h2><p class="lede">${escapeHTML(service?.nome_interno||'Serviço')} · preços por vigência</p></div><button class="close" data-close>×</button></div><div id="modalError"></div>
    ${table(['Vigência','Pagamento','Receber','Responsável','Ação'],prices.map(price=>[`${dateBR(price.inicio_vigencia)} — ${price.fim_vigencia?dateBR(price.fim_vigencia):'atual'}`,money(price.preco_pagamento),money(price.valor_receber_unitario),escapeHTML(price.atualizado_por_nome||price.criado_por_nome||'Sistema'),`<button class="text-button" data-price-correct="${price.id}">Corrigir</button>`]))}
    <form id="newPriceForm"><input type="hidden" name="servico_id" value="${serviceId}"><div class="form-grid price-form"><div class="field"><label>Novo preço de pagamento *</label><input name="preco_pagamento" type="number" min="0" step="0.0001" required value="${latest?.preco_pagamento??service?.preco_pagamento??0}"></div><div class="field"><label>Novo valor a receber *</label><input name="valor_receber_unitario" type="number" min="0" step="0.0001" required value="${latest?.valor_receber_unitario??service?.valor_receber_unitario??0}"></div><div class="field"><label>Início da vigência *</label><input name="inicio_vigencia" type="date" required></div><div class="field full"><label>Motivo *</label><textarea name="motivo" required placeholder="Ex.: novo preço contratual"></textarea></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar novo preço</button></div></form>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelectorAll('[data-price-correct]').forEach(button=>button.addEventListener('click',()=>priceCorrectionDialog(prices.find(price=>price.id===Number(button.dataset.priceCorrect)),service)));
  bindDialog('newPriceForm','/api/service-prices','Nova vigência de preço criada.');
}

async function employeeServicePriceDialog(serviceId) {
  const service = (state.rows.services||[]).find(item=>item.id===serviceId);
  if (!service) throw new Error('Serviço não encontrado. Atualize a página e tente novamente.');
  const [employees, prices] = await Promise.all([
    api(`/api/employees?obra_id=${state.obraId}`),
    api(`/api/employee-service-prices?obra_id=${state.obraId}&servico_id=${serviceId}`),
  ]);
  const activeEmployees = employees.filter(item=>item.ativo!==0);
  if (!activeEmployees.length) throw new Error('Não há funcionários ativos nesta obra.');
  const period = currentFortnightRange();
  const employeeOptions = activeEmployees.map(item=>`<option value="${item.id}">${escapeHTML(item.nome)}</option>`).join('');
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Preço individual</h2><p class="lede">${escapeHTML(service.nome_interno)} · pagamento ao funcionário</p></div><button class="close" data-close aria-label="Fechar">×</button></div><div id="modalError"></div>
    <p class="modal-notice">A nova tarifa vale desde ${dateBR(period.start)} até ${dateBR(period.end)} e continua nas próximas quinzenas, até outra vigência. Os lançamentos abertos desta quinzena são recalculados. Valor a receber da contratante não muda.</p>
    <form id="employeeServicePriceForm"><div class="form-grid"><div class="field full"><label>Funcionário *</label><select id="employeeServicePriceEmployee" name="funcionario_id" required>${employeeOptions}</select></div><div class="field"><label>Novo preço de pagamento *</label><input name="preco_pagamento" type="number" min="0" step="0.0001" required value="${service.preco_pagamento_atual??service.preco_pagamento??0}"></div><div class="field"><label>Motivo *</label><input name="motivo" maxlength="300" required placeholder="Ex.: valor combinado para este funcionário"></div></div><p class="field-help" id="employeeServicePriceCurrent"></p><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Salvar preço</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  const employeeSelect=dialog.querySelector('#employeeServicePriceEmployee');
  const currentHelp=dialog.querySelector('#employeeServicePriceCurrent');
  const refreshCurrent=()=>{
    const employeeId=Number(employeeSelect.value);
    const current=prices.find(price=>price.funcionario_id===employeeId&&price.inicio_vigencia<=period.start&&(!price.fim_vigencia||price.fim_vigencia>=period.start));
    currentHelp.textContent=current?`Preço individual vigente: ${money(current.preco_pagamento)} desde ${dateBR(current.inicio_vigencia)}.`:`Sem preço individual vigente. Será usado o preço-base de ${money(service.preco_pagamento_atual??service.preco_pagamento)}.`;
  };
  employeeSelect.addEventListener('change',refreshCurrent); refreshCurrent();
  dialog.querySelector('#employeeServicePriceForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const form=event.currentTarget; const submit=form.querySelector('.primary-button'); const payload=formPayload(form);
    submit.disabled=true; submit.textContent='Atualizando…';
    try {
      const result=await api('/api/employee-service-prices',{method:'POST',body:JSON.stringify({obra_id:state.obraId,servico_id:serviceId,funcionario_id:Number(payload.funcionario_id),preco_pagamento:Number(payload.preco_pagamento),motivo:payload.motivo})});
      const updated=result.impacto.lancamentos_atualizados,locked=result.impacto.lancamentos_bloqueados;
      dialog.close(); await renderServices();
      toast(`${money(result.preco.preco_pagamento)} salvo para ${dateBR(result.periodo.inicio)} em diante. ${updated} lançamento(s) atualizado(s).${locked?` ${locked} medido(s)/fechado(s) preservado(s).`:''}`);
    } catch(error) {
      dialog.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;
      submit.disabled=false; submit.textContent='Salvar preço';
    }
  });
}

function priceCorrectionDialog(price, service) {
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Corrigir preço</h2><p class="lede">Operação excepcional e auditada para ${escapeHTML(service?.nome_interno||'serviço')}.</p></div><button class="close" data-close>×</button></div><div class="modal-error">A correção pode afetar lançamentos do período. Registros históricos não serão recalculados silenciosamente.</div><form id="priceCorrectionForm"><input type="hidden" name="versao" value="${price.versao}"><div class="form-grid"><div class="field"><label>Valor anterior</label><input disabled value="${money(price.preco_pagamento)}"></div><div class="field"><label>Novo preço de pagamento *</label><input name="preco_pagamento" type="number" min="0" step="0.0001" required value="${price.preco_pagamento}"></div><div class="field"><label>Novo valor a receber *</label><input name="valor_receber_unitario" type="number" min="0" step="0.0001" required value="${price.valor_receber_unitario}"></div><div class="field full"><label>Motivo da correção *</label><textarea name="motivo_correcao" required></textarea></div></div><div class="modal-footer"><button type="button" class="secondary-button" data-close>Cancelar</button><button class="primary-button">Confirmar correção</button></div></form></div>`;
  dialog.showModal(); bindDialog('priceCorrectionForm',`/api/service-prices/${price.id}`,'Preço corrigido com auditoria.','PUT');
}

async function historyDialog(entity, id, title) {
  const rows = await api(`/api/history?entity=${encodeURIComponent(entity)}&id=${id}`);
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>Histórico</h2><p class="lede">${escapeHTML(title)}</p></div><button class="close" data-close>×</button></div>${table(['Data','Usuário','Ação','Anterior','Novo'],rows.map(item=>[escapeHTML((item.criado_em||'').replace('T',' ').slice(0,19)),escapeHTML(item.usuario||'Sistema'),status(item.acao),`<code class="audit-json">${escapeHTML(item.antes||'—')}</code>`,`<code class="audit-json">${escapeHTML(item.depois||item.detalhes||'—')}</code>`]),'Nenhuma alteração registrada.') }<div class="modal-footer"><button class="secondary-button" data-close>Fechar</button></div></div>`;
  dialog.showModal(); dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
}

function passwordDialog(required = false, targetId = null, targetName = '') {
  const reset = targetId !== null;
  dialog.innerHTML = `<div class="modal"><div class="modal-head"><div><h2>${reset?'Redefinir senha':required?'Proteja sua conta':'Alterar senha'}</h2><p class="lede">${reset?`Defina uma senha temporária para ${escapeHTML(targetName)}.`:'Crie uma senha pessoal antes de usar o sistema.'}</p></div>${required?'':'<button class="close" data-close aria-label="Fechar">×</button>'}</div><div id="modalError"></div><form id="passwordForm"><div class="form-grid">${reset?'':`<div class="field full"><label>Senha atual *</label><input name="current_password" type="password" autocomplete="current-password" required><small class="field-help"></small></div>`}<div class="field full"><label>Nova senha *</label><input name="new_password" type="password" autocomplete="new-password" required><small class="field-help">8 caracteres, maiúscula, minúscula, número e símbolo.</small></div></div><div class="modal-footer">${required||reset?'':'<button type="button" class="secondary-button" data-close>Cancelar</button>'}<button class="primary-button">Salvar nova senha</button></div></form></div>`;
  dialog.showModal();
  dialog.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.querySelector('#passwordForm').addEventListener('submit', async event=>{
    event.preventDefault(); const payload=formPayload(event.currentTarget); const submit=event.currentTarget.querySelector('.primary-button'); submit.disabled=true; submit.textContent='Salvando…';
    try { if(reset) await api(`/api/users/${targetId}/reset-password`,{method:'POST',body:JSON.stringify({password:payload.new_password})}); else await api('/api/auth/change-password',{method:'POST',body:JSON.stringify(payload)}); dialog.close(); if(reset){toast('Senha temporária redefinida.');render();}else{sessionStorage.removeItem('pms_csrf');window.location.replace('/login.html');} }
    catch(error){document.querySelector('#modalError').innerHTML=`<div class="modal-error">${escapeHTML(error.message)}</div>`;submit.disabled=false;submit.textContent='Tentar novamente';}
  });
}

function formPayload(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.obra_id = state.obraId;
  if (form.querySelector('[name="obra_ids"]')) payload.obra_ids = [...form.querySelectorAll('[name="obra_ids"]:checked')].map(input=>Number(input.value));
  ['funcionario_id','servico_id','pms_numero','numero','lancamento_id','fechamento_id','item_id','versao','ativo'].forEach(key => { if (payload[key] !== undefined && payload[key] !== '') payload[key] = Number(payload[key]); else if (key in payload) payload[key]=null; });
  ['quantidade_m2','horas_trabalhadas','extra','desconto','preco_pagamento','valor_receber_unitario','quantidade_padrao','valor','valor_novo'].forEach(key => { if (key in payload) payload[key] = payload[key] === '' ? null : Number(payload[key]); });
  return payload;
}

function bindDialog(formId, endpoint, success, method = 'POST') {
  dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  const form = document.querySelector(`#${formId}`);
  form.addEventListener('submit', async event => {
    event.preventDefault(); const submit = form.querySelector('[type="submit"],button:not([type])'); submit.disabled = true; submit.textContent = 'Salvando…';
    try { await api(endpoint,{method,body:JSON.stringify(formPayload(form))}); dialog.close(); toast(success); await loadWorks(); await loadBootstrap(); await render(); }
    catch (error) { document.querySelector('#modalError').innerHTML = `<div class="modal-error">${escapeHTML(error.message)}</div>`; submit.disabled=false; submit.textContent='Tentar novamente'; }
  });
}

function bindViewEvents() {
  app.querySelectorAll('[data-view]').forEach(element => element.addEventListener('click', () => render(element.dataset.view)));
  app.querySelectorAll('[data-settings-section]').forEach(element => element.addEventListener('click', () => { state.settingsSection=element.dataset.settingsSection; state.settingsAuditPage=1; render('settings'); }));
  app.querySelector('#settingsSectionSelect')?.addEventListener('change', event => { state.settingsSection=event.target.value; state.settingsAuditPage=1; render('settings'); });
  app.querySelectorAll('[data-open-quality]').forEach(element => element.addEventListener('click', () => { state.qualityFilter = element.dataset.openQuality; state.qualityPage = 1; render('quality'); }));
  app.querySelectorAll('[data-quality-filter]').forEach(element => element.addEventListener('click', () => { state.qualityFilter = element.dataset.qualityFilter; state.qualityPage = 1; render('quality'); }));
  app.querySelectorAll('[data-quality-page]').forEach(element => element.addEventListener('click', () => { state.qualityPage = Number(element.dataset.qualityPage); render('quality'); }));
  app.querySelectorAll('[data-action="new-entry"]').forEach(element => element.addEventListener('click', entryDialog));
  app.querySelectorAll('[data-action="new-appropriation"]').forEach(element => element.addEventListener('click', appropriationDialog));
  app.querySelectorAll('[data-action="new-closing"]').forEach(element => element.addEventListener('click', closingDialog));
  app.querySelectorAll('[data-action="new-payment-proposal"]').forEach(element=>element.addEventListener('click',paymentProposalDialog));
  app.querySelectorAll('[data-action="payment-proposal-add-item"]').forEach(element=>element.addEventListener('click',paymentProposalItemDialog));
  app.querySelectorAll('[data-action="new-pms"]').forEach(element => element.addEventListener('click', pmsDialog));
  app.querySelectorAll('[data-action="new-proposal"]').forEach(element => element.addEventListener('click', proposalDialog));
  app.querySelector('#proposalSelect')?.addEventListener('change',event=>{state.rows.proposalId=Number(event.target.value);render('proposals');});
  app.querySelector('#paymentProposalSelect')?.addEventListener('change',event=>{state.rows.paymentProposalId=Number(event.target.value);render('payments');});
  app.querySelectorAll('[data-payment-field]').forEach(input=>input.addEventListener('change',async()=>{
    const proposal=state.rows.paymentProposal;const rowId=Number(input.closest('[data-payment-row]').dataset.paymentRow);const key=input.dataset.paymentField;
    const value=['quantidade','valor_pagar'].includes(key)?Number(input.value||0):input.value;
    try{await api(`/api/payment-proposals/${proposal.id}/items/${rowId}`,{method:'PUT',body:JSON.stringify({[key]:value})});toast('Proposta atualizada.');await render('payments');}
    catch(error){toast(error.message,'error');await render('payments');}
  }));
  app.querySelectorAll('[data-payment-item-remove]').forEach(button=>button.addEventListener('click',async()=>{
    if(!window.confirm('Remover esta linha da proposta? A produção original será preservada.'))return;
    try{await api(`/api/payment-proposals/${state.rows.paymentProposal.id}/items/${button.dataset.paymentItemRemove}`,{method:'DELETE'});toast('Linha removida da proposta.');await render('payments');}
    catch(error){toast(error.message,'error');}
  }));
  app.querySelectorAll('[data-proposal-add-service]').forEach(button=>button.addEventListener('click',async()=>{const nome=window.prompt('Nome do serviço');if(!nome)return;try{await api(`/api/proposals/${state.rows.proposal.id}/services`,{method:'POST',body:JSON.stringify({nome_servico:nome})});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-add-item]').forEach(button=>button.addEventListener('click',async()=>{try{await api(`/api/proposals/${state.rows.proposal.id}/services/${button.dataset.proposalAddItem}/items`,{method:'POST',body:JSON.stringify({bloco:'',casa:''})});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-move]').forEach(button=>button.addEventListener('click',async()=>{const ids=state.rows.proposal.servicos.map(item=>item.id);const index=ids.indexOf(Number(button.dataset.proposalServiceId));const target=index+(button.dataset.proposalMove==='up'?-1:1);if(target<0||target>=ids.length)return;[ids[index],ids[target]]=[ids[target],ids[index]];try{await api(`/api/proposals/${state.rows.proposal.id}/reorder`,{method:'POST',body:JSON.stringify({servicos:ids})});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-service-delete]').forEach(button=>button.addEventListener('click',async()=>{if(!window.confirm('Excluir este serviço e suas casas?'))return;try{await api(`/api/proposals/${state.rows.proposal.id}/services/${button.dataset.proposalServiceDelete}`,{method:'DELETE'});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-item-delete]').forEach(button=>button.addEventListener('click',async()=>{try{await api(`/api/proposals/${state.rows.proposal.id}/items/${button.dataset.proposalItemDelete}`,{method:'DELETE'});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-service]').forEach(input=>input.addEventListener('change',async()=>{try{await api(`/api/proposals/${state.rows.proposal.id}/services/${input.dataset.proposalService}`,{method:'PUT',body:JSON.stringify({nome_servico:input.value})});render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-proposal-item-block],[data-proposal-item-house]').forEach(input=>input.addEventListener('change',async()=>{const proposal=state.rows.proposal;try{if(input.hasAttribute('data-proposal-item-block')){const itemIds=input.dataset.proposalBlockItemIds.split(',').filter(Boolean).map(Number);await api(`/api/proposals/${proposal.id}/blocks`,{method:'PUT',body:JSON.stringify({item_ids:itemIds,bloco:input.value})});}else{const id=Number(input.dataset.proposalItemHouse);const item=proposal.servicos.flatMap(service=>service.itens).find(row=>row.id===id);await api(`/api/proposals/${proposal.id}/items/${id}`,{method:'PUT',body:JSON.stringify({bloco:item.bloco,casa:input.value})});}render('proposals');}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-action="new-reimbursement"]').forEach(element => element.addEventListener('click', reimbursementDialog));
  app.querySelectorAll('[data-action="new-employee"]').forEach(element => element.addEventListener('click', employeeDialog));
  app.querySelectorAll('[data-action="new-service"]').forEach(element => element.addEventListener('click', serviceDialog));
  app.querySelectorAll('[data-action="new-user"]').forEach(element => element.addEventListener('click', userDialog));
  app.querySelectorAll('[data-action="edit-work"]').forEach(element => element.addEventListener('click', workEditDialog));
  app.querySelectorAll('[data-entry-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.entries||[]).find(row=>row.id===Number(element.dataset.entryEdit));if(item)entryEditDialog(item);}));
  app.querySelectorAll('[data-entry-delete]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.entries||[]).find(row=>row.id===Number(element.dataset.entryDelete));if(item)entryDeleteDialog(item);}));
  app.querySelectorAll('[data-employee-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.employees||[]).find(row=>row.id===Number(element.dataset.employeeEdit));if(item)employeeDialog(item);}));
  app.querySelectorAll('[data-service-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.services||[]).find(row=>row.id===Number(element.dataset.serviceEdit));if(item)serviceDialog(item);}));
  app.querySelectorAll('[data-user-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.users||[]).find(row=>row.id===Number(element.dataset.userEdit));if(item)userEditDialog(item);}));
  app.querySelectorAll('[data-price-history]').forEach(element => element.addEventListener('click',()=>priceHistoryDialog(Number(element.dataset.priceHistory)).catch(error=>toast(error.message,'error'))));
  app.querySelectorAll('[data-employee-price]').forEach(element => element.addEventListener('click',()=>employeeServicePriceDialog(Number(element.dataset.employeePrice)).catch(error=>toast(error.message,'error'))));
  app.querySelectorAll('[data-history-entity]').forEach(element => element.addEventListener('click',()=>historyDialog(element.dataset.historyEntity,Number(element.dataset.historyId),element.dataset.historyTitle).catch(error=>toast(error.message,'error'))));
  app.querySelectorAll('[data-appropriation-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.appropriations||[]).find(row=>row.id===Number(element.dataset.appropriationEdit));if(item)appropriationEditDialog(item);}));
  app.querySelectorAll('[data-reimbursement-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.reimbursements||[]).find(row=>row.id===Number(element.dataset.reimbursementEdit));if(item)reimbursementEditDialog(item);}));
  app.querySelectorAll('[data-forecast-edit]').forEach(element => element.addEventListener('click',()=>{const item=(state.rows.forecast||[]).find(row=>row.id===Number(element.dataset.forecastEdit));if(item)forecastEditDialog(item);}));
  app.querySelectorAll('[data-quality-history]').forEach(element => element.addEventListener('click',()=>qualityHistoryDialog(Number(element.dataset.qualityHistory)).catch(error=>toast(error.message,'error'))));
  app.querySelectorAll('[data-quality]').forEach(element => element.addEventListener('click',()=>qualityDialog(element)));
  app.querySelectorAll('[data-quality-ok]').forEach(element => element.addEventListener('click',()=>quickQualityOK(element)));
  app.querySelectorAll('[data-deactivate]').forEach(element => element.addEventListener('click',async()=>{if(!window.confirm('Deseja inativar este cadastro? O histórico será preservado.'))return;try{await api(`/api/${element.dataset.deactivate}/${element.dataset.id}`,{method:'DELETE'});toast('Cadastro inativado com histórico preservado.');await loadBootstrap();await render();}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-user-toggle]').forEach(element => element.addEventListener('click', async()=>{try{await api(`/api/users/${element.dataset.userToggle}/toggle`,{method:'POST',body:'{}'});toast('Acesso atualizado.');render();}catch(error){toast(error.message,'error');}}));
  app.querySelectorAll('[data-user-reset]').forEach(element => element.addEventListener('click',()=>passwordDialog(false,Number(element.dataset.userReset),element.dataset.userName)));
  app.querySelectorAll('[data-action="open-password"]').forEach(element=>element.addEventListener('click',()=>passwordDialog(false)));
  app.querySelectorAll('[data-action="settings-remove-logo"]').forEach(element=>element.addEventListener('click',()=>{
    const form=app.querySelector('#settingsCompanyForm');if(!form)return;
    form.elements.remover_logo.value='true';form.elements.logo.value='';
    const preview=app.querySelector('.settings-logo-preview');if(preview)preview.remove();
    const empty=app.querySelector('.settings-logo-empty');if(!empty){const node=document.createElement('div');node.className='settings-logo-empty';node.textContent='Sem marca';app.querySelector('.settings-logo-row').prepend(node);}
  }));
  app.querySelector('#settingsCompanyForm')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;const payload=Object.fromEntries(new FormData(form).entries());
    payload.remover_logo=form.elements.remover_logo.value==='true';delete payload.logo;
    const file=form.elements.logo.files[0];
    try{
      if(file){if(file.size>2*1024*1024)throw new Error('A marca deve ter até 2 MB');payload.logo_mime=file.type;payload.logo_base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]||'');reader.onerror=()=>reject(new Error('Não foi possível ler a marca'));reader.readAsDataURL(file);});}
      await api('/api/settings/company',{method:'PUT',body:JSON.stringify(payload)});toast('Dados da empresa atualizados.');render('settings');
    }catch(error){toast(error.message,'error');}
  });
  app.querySelector('#settingsWorkForm')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;const payload=Object.fromEntries(new FormData(form).entries());
    payload.bloquear_datas_futuras=form.elements.bloquear_datas_futuras.checked;payload.politica_coletivo='dividir_igualmente';
    try{await api(`/api/settings/work?obra_id=${state.obraId}`,{method:'PUT',body:JSON.stringify(payload)});toast('Regras da obra salvas.');render('settings');}
    catch(error){toast(error.message,'error');}
  });
  app.querySelector('#settingsAccountForm')?.addEventListener('submit',async event=>{
    event.preventDefault();try{await api('/api/settings/account',{method:'PUT',body:JSON.stringify(formPayload(event.currentTarget))});await loadCurrentUser();toast('Dados pessoais atualizados.');render('settings');}catch(error){toast(error.message,'error');}
  });
  app.querySelector('#settingsPreferencesForm')?.addEventListener('submit',async event=>{
    event.preventDefault();const payload=formPayload(event.currentTarget);payload.obra_padrao_id=payload.obra_padrao_id||null;payload.linhas_por_pagina=Number(payload.linhas_por_pagina);
    try{await api('/api/settings/preferences',{method:'PUT',body:JSON.stringify(payload)});toast('Preferências salvas.');render('settings');}catch(error){toast(error.message,'error');}
  });
  app.querySelectorAll('[data-action="logout-other-sessions"]').forEach(element=>element.addEventListener('click',async()=>{
    if(!window.confirm('Encerrar as outras sessões abertas da sua conta?'))return;
    try{const result=await api('/api/auth/logout-others',{method:'POST',body:'{}'});toast(`${result.encerradas} sessão(ões) encerrada(s).`);render('settings');}catch(error){toast(error.message,'error');}
  }));
  app.querySelectorAll('[data-field-link-save]').forEach(button=>button.addEventListener('click',async()=>{
    const key=button.dataset.fieldLinkSave;const split=key.indexOf(':');const tipo=key.slice(0,split);const id_origem=key.slice(split+1);
    const select=[...app.querySelectorAll('[data-field-destination]')].find(item=>item.dataset.fieldDestination===`${tipo}:${id_origem}`);
    if(!select?.value){toast('Escolha um cadastro central para vincular.','error');return;}
    try{await api(`/api/settings/field-links?obra_id=${state.obraId}`,{method:'PUT',body:JSON.stringify({tipo,id_origem,id_destino:Number(select.value)})});toast('Vínculo salvo.');render('settings');}catch(error){toast(error.message,'error');}
  }));
  app.querySelectorAll('[data-field-import]').forEach(button=>button.addEventListener('click',async()=>{
    const appointment=(state.rows.fieldAppointments||[]).find(row=>String(row.id)===button.dataset.fieldImport);
    if(!appointment)return;
    if(!window.confirm(`Importar ${appointment.quantidade??'a quantidade registrada'} ${appointment.unidade||''} de ${appointment.servico}, dividida igualmente entre ${appointment.funcionarios.join(', ')}? Serão criados lançamentos de produção, sem medição ou pagamento.`))return;
    button.disabled=true;button.textContent='Importando…';
    try{const result=await api(`/api/settings/field-import?obra_id=${state.obraId}`,{method:'POST',body:JSON.stringify({apontamento_id:appointment.id})});toast(`${result.lancamentos.length} lançamento(s) criado(s); total ${number(result.quantidade_total)}.`);await loadBootstrap();render('settings');}
    catch(error){toast(error.message,'error');button.disabled=false;button.textContent='Importar para produção';}
  }));
  app.querySelector('#settingsAuditForm')?.addEventListener('submit',event=>{event.preventDefault();state.settingsAuditFilters=Object.fromEntries(new FormData(event.currentTarget).entries());state.settingsAuditPage=1;render('settings');});
  app.querySelectorAll('[data-audit-page]').forEach(button=>button.addEventListener('click',()=>{state.settingsAuditPage=Number(button.dataset.auditPage);render('settings');}));
  app.querySelectorAll('[data-action="refresh-settings"]').forEach(element=>element.addEventListener('click',()=>render('settings')));
  app.querySelector('#settingsRestoreForm')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;const file=form.elements.backup.files[0];const confirmation=form.elements.confirmacao.value.trim();
    if(!file||file.size>128*1024*1024){toast('Selecione um arquivo SQLite de até 128 MB.','error');return;}
    if(confirmation!=='RESTAURAR'||!window.confirm('Esta ação substitui todos os dados do sistema O gestor de Campo. Um backup de segurança será criado antes. Continuar?'))return;
    const button=form.querySelector('button');button.disabled=true;button.textContent='Validando e restaurando…';
    try{const response=await fetch('/api/settings/restore',{method:'POST',headers:{'Content-Type':'application/vnd.sqlite3','X-CSRF-Token':state.csrf,'X-Restore-Confirmation':confirmation},body:file});const result=await response.json();if(!response.ok)throw new Error(result.error||'Não foi possível restaurar o banco.');alert(`Banco restaurado. O backup anterior foi salvo como ${result.backup_automatico}. Entre novamente.`);sessionStorage.removeItem('pms_csrf');window.location.replace('/login.html');}
    catch(error){toast(error.message,'error');button.disabled=false;button.textContent='Tentar novamente';}
  });
  app.querySelectorAll('[data-action="retry"]').forEach(element => element.addEventListener('click', () => render()));
  app.querySelectorAll('[data-page]').forEach(element => element.addEventListener('click', () => { state.page=Number(element.dataset.page); render('entries'); }));
  const search = document.querySelector('#entrySearch');
  const runSearch = () => { state.search=search?.value.trim() || ''; state.page=1; render('entries'); };
  app.querySelectorAll('[data-action="search"]').forEach(element => element.addEventListener('click', runSearch));
  search?.addEventListener('keydown', event => { if (event.key === 'Enter') runSearch(); });
}

document.querySelectorAll('.nav-item[data-view]').forEach(item => item.addEventListener('click', () => { render(item.dataset.view); document.querySelector('#sidebar').classList.remove('open'); }));
document.querySelector('.sidebar-bottom [data-view]')?.addEventListener('click',()=>render('settings'));
document.querySelector('#mobileMenu').addEventListener('click', () => document.querySelector('#sidebar').classList.toggle('open'));
document.querySelector('#projectPicker').addEventListener('click', projectDialog);
document.querySelector('#periodButton').addEventListener('click', periodDialog);
document.querySelector('#changePasswordButton').addEventListener('click',()=>passwordDialog(false));
document.querySelector('#logoutButton').addEventListener('click', async()=>{try{await api('/api/auth/logout',{method:'POST',body:'{}'});}finally{sessionStorage.removeItem('pms_csrf');for(const key of Object.keys(localStorage))if(key.startsWith('pms_field_access_'))localStorage.removeItem(key);window.location.replace('/login.html');}});
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });

(async function start() {
  if(location.protocol==='file:')return;
  loading();
  try {
    await loadCurrentUser(); if(state.currentUser.deve_trocar_senha){passwordDialog(true);return;}
    await loadWorks();
    let initial=await api(`/api/settings?obra_id=${state.obraId}`);
    if(!sessionStorage.getItem('pms_work_id')&&initial.preferences.obra_padrao_id&&state.works.some(w=>Number(w.id)===Number(initial.preferences.obra_padrao_id))){
      state.obraId=Number(initial.preferences.obra_padrao_id);initial=await api(`/api/settings?obra_id=${state.obraId}`);
    }
    state.pageSize=Number(initial.preferences.linhas_por_pagina)||25;
    await loadBootstrap();
    if(initialRoute.get('view')==='settings')state.settingsSection=initialRoute.get('section')||'account';
    const defaultView=['pms','forecast'].includes(initial.preferences.tela_inicial)?'payments':(initial.preferences.tela_inicial||'overview');
    await render(initialRoute.get('view')==='settings'?'settings':(initialRoute.get('view')||defaultView));
  }
  catch (error) { if(String(error.message).includes('autenticado')||String(error.message).includes('expirada')){window.location.replace('/login.html');return;} app.innerHTML = `<div class="error-state"><div><strong>Servidor do sistema O gestor de Campo indisponível.</strong>${escapeHTML(error.message)}<br>Execute <code>python3 server.py</code> nesta pasta.</div></div>`; }
})();
