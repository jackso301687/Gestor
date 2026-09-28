'use strict';

const requestedWork = Number(new URLSearchParams(location.search).get('obra_id') || localStorage.getItem('pms_field_work') || 1);
const WORK_ID = Number.isSafeInteger(requestedWork) && requestedWork > 0 ? requestedWork : 1;
const ACCESS_KEY = `pms_field_access_${WORK_ID}`;
const DB_NAME = `pms_apontamento_obra_${WORK_ID}`;
const DB_VERSION = 1;
const STORES = ['employees','services','locations','appointments','settings','audit'];
const SYNC_STORES = ['employees','services','locations','appointments','settings','audit'];
const WORKS_CACHE_KEY = 'pms_field_works';

const state = {
  route: 'today',
  selectedDate: todayISO(),
  db: null,
  deferredInstall: null,
  pointMultiMode: false,
  pointSelectedEmployees: new Set(),
  syncState: 'local',
  settings: null,
  currentUser: null,
  workName: '',
  pointEmployeeQuery: '',
  pendingRecords: 0
};

const $ = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
const main = () => $('#mainContent');

function uid(){ return crypto.randomUUID ? crypto.randomUUID() : 'id-'+Date.now()+'-'+Math.random().toString(16).slice(2); }
function nowISO(){ return new Date().toISOString(); }
function todayISO(){ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }
function dateAdd(iso, days){ const [y,m,d]=iso.split('-').map(Number); const x=new Date(y,m-1,d); x.setDate(x.getDate()+days); return `${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}`; }
function formatDate(iso, long=false){ if(!iso) return ''; const [y,m,d]=iso.split('-').map(Number); const dt=new Date(y,m-1,d); return new Intl.DateTimeFormat('pt-BR', long?{weekday:'long',day:'2-digit',month:'long',year:'numeric'}:{day:'2-digit',month:'2-digit',year:'numeric'}).format(dt); }
function formatTime(d=new Date()){ return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`; }
function esc(s=''){ return String(s).replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function normalize(s=''){ return String(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase(); }
function sortName(a,b){ return a.name.localeCompare(b.name,'pt-BR',{sensitivity:'base'}); }
function isFuture(iso){ return iso > todayISO(); }
function statusLabel(s){ return ({em_andamento:'Em andamento',finalizado:'Concluído no campo',pendente:'Com pendência',pausado:'Pausado'})[s]||s; }
function statusBadge(s){ const cls=s==='finalizado'?'badge-success':s==='pendente'?'badge-danger':s==='em_andamento'?'badge-warning':'badge-muted'; return `<span class="badge ${cls}"><span class="status-dot status-${esc(s)}"></span>${esc(statusLabel(s))}</span>`; }
function initials(name=''){ return name.trim().split(/\s+/).slice(0,2).map(x=>x[0]?.toUpperCase()||'').join(''); }
function roundQty(v){ const n=Number(v); return Number.isInteger(n)?String(n):n.toLocaleString('pt-BR',{maximumFractionDigits:2}); }
function fileSafe(s){ return normalize(s).replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,''); }
function downloadBlob(blob, filename){ const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=filename; document.body.appendChild(a); a.click(); setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},500); }

function toast(message, ms=2200){ const el=$('#toast'); el.textContent=message; el.classList.remove('hidden'); clearTimeout(toast.t); toast.t=setTimeout(()=>el.classList.add('hidden'),ms); }

function openDB(){
  return new Promise((resolve,reject)=>{
    const req=indexedDB.open(DB_NAME,DB_VERSION);
    req.onupgradeneeded=()=>{
      const db=req.result;
      for(const s of STORES){ if(!db.objectStoreNames.contains(s)) db.createObjectStore(s,{keyPath:'id'}); }
    };
    req.onsuccess=()=>resolve(req.result); req.onerror=()=>reject(req.error);
  });
}
function tx(store, mode='readonly'){ return state.db.transaction(store,mode).objectStore(store); }
function dbGetAll(store){ return new Promise((res,rej)=>{ const r=tx(store).getAll(); r.onsuccess=()=>res(r.result||[]); r.onerror=()=>rej(r.error); }); }
function dbGet(store,id){ return new Promise((res,rej)=>{ const r=tx(store).get(id); r.onsuccess=()=>res(r.result); r.onerror=()=>rej(r.error); }); }
function dbPut(store,obj){ return new Promise((res,rej)=>{ const t=state.db.transaction(store,'readwrite'); t.objectStore(store).put(obj); t.oncomplete=()=>res(obj); t.onabort=t.onerror=()=>rej(t.error||new Error('Falha ao salvar no aparelho')); }); }
function dbImportCatalog(store, rows){return new Promise((res,rej)=>{const t=state.db.transaction([store,'audit'],'readwrite'), at=nowISO();for(const row of rows){const id=uid();t.objectStore(store).put({...row,id,updatedAt:at});t.objectStore('audit').put({id:uid(),action:'importou',entity:store==='employees'?'employee':'service',entityId:id,details:row.name,at,updatedAt:at});}t.oncomplete=()=>res();t.onabort=t.onerror=()=>rej(t.error||new Error('Falha ao importar cadastros'));});}
function dbDelete(store,id){ return new Promise((res,rej)=>{ const r=tx(store,'readwrite').delete(id); r.onsuccess=()=>res(); r.onerror=()=>rej(r.error); }); }
function visible(rows){ return rows.filter(x=>!x._deleted); }

async function audit(action, entity, entityId, details=''){
  const rec={id:uid(),action,entity,entityId,details,at:nowISO(),updatedAt:nowISO()};
  await dbPut('audit',rec); scheduleSync();
}

async function loadSettings(){
  let s=await dbGet('settings','app');
  if(!s){ s={id:'app',companyName:'',workspaceName:'',responsible:'',accent:'#167a62',blockFutureDates:true,updatedAt:nowISO()}; }
  state.settings=s;
  const label=state.workName||s.workspaceName||'Obra';
  $('#workspaceLabel').textContent = `APONTAMENTO DE CAMPO · ${label.toUpperCase()}`;
  $('#sidebarWork').textContent=label;
}

function populateWorkSelector(works){
  const selector=$('#workSelector');
  if(!selector)return;
  const available=(Array.isArray(works)?works:[]).filter(work=>Number.isSafeInteger(Number(work.id))&&Number(work.id)>0&&typeof work.nome==='string');
  selector.replaceChildren();
  for(const work of available){
    const option=document.createElement('option');
    option.value=String(Number(work.id));
    option.textContent=work.nome;
    selector.appendChild(option);
  }
  selector.disabled=!available.some(work=>Number(work.id)===WORK_ID);
  if(!selector.disabled)selector.value=String(WORK_ID);
}

function validateSnapshot(snapshot){
  if(!snapshot || typeof snapshot!=='object' || Array.isArray(snapshot) || Object.keys(snapshot).some(s=>!SYNC_STORES.includes(s))) throw new Error('Formato de backup inválido');
  for(const store of SYNC_STORES){
    const rows=snapshot[store]||[];
    if(!Array.isArray(rows)||rows.length>25000) throw new Error(`Lista inválida: ${store}`);
    for(const row of rows){
      if(!row || typeof row!=='object' || typeof row.id!=='string' || !/^[\w:.-]{1,200}$/.test(row.id) || typeof row.updatedAt!=='string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(row.updatedAt) || !Number.isFinite(Date.parse(row.updatedAt))) throw new Error(`Registro inválido: ${store}`);
      for(const key of ['active','_deleted','requiresQuantity','blockFutureDates']) if(key in row && typeof row[key]!=='boolean') throw new Error(`Campo inválido: ${key}`);
      for(const key of ['name','label','role','team','category','unit','type','observation','companyName','workspaceName','responsible','action','entity','entityId','details']) if(key in row && (typeof row[key]!=='string'||row[key].length>10000)) throw new Error(`Texto inválido: ${key}`);
      if(row._deleted) continue;
      const label=({employees:'name',services:'name',locations:'label'})[store];
      if(label && (typeof row[label]!=='string'||!row[label].trim())) throw new Error(`Cadastro sem ${label}`);
      if(store==='appointments' && (!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||!Number.isFinite(Date.parse(row.date))||new Date(row.date).toISOString().slice(0,10)!==row.date||!['em_andamento','finalizado','pendente','pausado'].includes(row.status)||!Array.isArray(row.employeeIds)||!row.employeeIds.length||!Array.isArray(row.locationIds)||!row.locationIds.length||[...row.employeeIds,...row.locationIds,row.serviceId].some(id=>typeof id!=='string'||!id)||row.quantity!=null&&(typeof row.quantity!=='number'||!Number.isFinite(row.quantity)||row.quantity<0))) throw new Error('Apontamento inválido no backup');
      if(store==='settings' && row.id!=='app') throw new Error('Configuração inválida');
    }
  }
  return snapshot;
}
async function validateBackupRelations(snapshot){
  const local=await buildLocalSnapshot();
  const combined={};
  for(const store of ['employees','services','locations','appointments']){
    const records=new Map(local[store].map(row=>[row.id,row]));
    for(const row of snapshot[store]||[]){const current=records.get(row.id);if(!current||Date.parse(row.updatedAt)>=Date.parse(current.updatedAt))records.set(row.id,row);}
    combined[store]=records;
  }
  for(const row of combined.appointments.values()){
    if(row._deleted)continue;
    for(const [store,ids] of [['employees',row.employeeIds],['locations',row.locationIds],['services',[row.serviceId]]])
      if(ids.some(id=>!combined[store].has(id)))throw new Error(`Apontamento refere ${store} ausentes`);
  }
}
function applyRemoteSnapshot(snapshot){
  validateSnapshot(snapshot);
  // Uma transação mantém versões locais mais recentes; empate segue a regra do merge do servidor.
  return new Promise((resolve,reject)=>{
    const t=state.db.transaction(SYNC_STORES,'readwrite');
    t.oncomplete=resolve; t.onabort=t.onerror=()=>reject(t.error||new Error('Falha ao importar dados'));
    for(const store of SYNC_STORES) for(const remote of snapshot[store]||[]){
      const s=t.objectStore(store), request=s.get(remote.id);
      // Em empate de horário, o servidor é a fonte canônica (ex.: nomes normalizados).
      // O merge do servidor também aceita o registro recebido quando updatedAt é igual.
      request.onsuccess=()=>{const local=request.result;if(store==='settings'||!local||Date.parse(remote.updatedAt)>=Date.parse(local.updatedAt))s.put(remote);};
    }
  });
}
async function fieldRequest(path, options={}){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(12000),...options});
  const data=await response.json();
  if(!response.ok){
    if(response.status===401||response.status===403) localStorage.removeItem(ACCESS_KEY);
    throw new Error(data.error||`Falha no servidor (${response.status})`);
  }
  return data;
}
async function buildLocalSnapshot(){ const out={}; for(const s of SYNC_STORES) out[s]=await dbGetAll(s); return out; }
let syncInFlight=null;
function syncNow(silent=true){
  if(!syncInFlight) syncInFlight=performSync(silent).finally(()=>{syncInFlight=null;});
  return syncInFlight;
}
async function performSync(silent=true){
  if(!navigator.onLine){ state.syncState='offline'; updateConnectionBadge(); return false; }
  try{
    state.syncState='syncing'; updateConnectionBadge();
    // Recupera o CSRF da sessão atual também após abrir outra aba/reiniciar o PWA.
    const auth=await fieldRequest('/api/auth/me');
    sessionStorage.setItem('pms_csrf',auth.csrf_token);
    await applyRemoteSnapshot(await fieldRequest(`/api/apontamento/snapshot?obra_id=${WORK_ID}`));
    const payload=await buildLocalSnapshot();
    const merged=await fieldRequest(`/api/apontamento/sync?obra_id=${WORK_ID}`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':auth.csrf_token},body:JSON.stringify(payload)});
    if(!merged.ok||!merged.snapshot) throw new Error('O servidor não confirmou a gravação');
    await applyRemoteSnapshot(merged.snapshot);
    const current=await buildLocalSnapshot();
    const pendingRows=SYNC_STORES.flatMap(s=>current[s].filter(row=>!merged.snapshot[s]?.some(saved=>saved.id===row.id&&JSON.stringify(saved)===JSON.stringify(row))));
    const pending=pendingRows.length>0;
    state.pendingRecords=pendingRows.length;
    state.syncState=pending?'local':'synced'; updateConnectionBadge(); await loadSettings();
    localStorage.setItem(ACCESS_KEY,JSON.stringify({user:auth.user,workName:state.workName,expiresAt:Date.parse(auth.expires_at)}));
    if(pending){scheduleSync();return false;}
    if(!silent) toast('Dados sincronizados'); return true;
  }catch(e){ state.syncState='local'; updateConnectionBadge(); if(!silent) toast(`${e.message}. Os dados continuam neste aparelho.`,6000); return false; }
}
let syncTimer=null;
function scheduleSync(){ state.pendingRecords=Math.max(1,state.pendingRecords);state.syncState=navigator.onLine?'local':'offline';updateConnectionBadge();clearTimeout(syncTimer); syncTimer=setTimeout(()=>syncNow(true),700); }
function updateConnectionBadge(){
  const el=$('#connectionBadge'); if(!el)return;
  if(!navigator.onLine||state.syncState==='offline'){el.className='badge badge-warning';el.textContent='Offline';el.title='Sem internet. Os dados ficam salvos neste aparelho.';}
  else if(state.syncState==='syncing'){el.className='badge badge-info';el.textContent='Sincronizando';el.title='Enviando os dados para o servidor.';}
  else if(state.syncState==='synced'){el.className='badge badge-success';el.textContent='Sincronizado';el.title='Todos os dados deste aparelho foram enviados.';}
  else {el.className='badge badge-warning';el.textContent=state.pendingRecords?`Salvo no aparelho · ${state.pendingRecords} pendente${state.pendingRecords===1?'':'s'}`:'Salvo no aparelho';el.title='Os dados estão seguros neste aparelho e aguardam sincronização.';}
}

async function getData(){
  const [employees,services,locations,appointments]=await Promise.all(['employees','services','locations','appointments'].map(dbGetAll));
  return {employees,services,locations,appointments:visible(appointments)};
}
function empMap(data){ return new Map(data.employees.map(x=>[x.id,x])); }
function svcMap(data){ return new Map(data.services.map(x=>[x.id,x])); }
function locMap(data){ return new Map(data.locations.map(x=>[x.id,x])); }
function recordLabel(a,data){
  const em=empMap(data), sm=svcMap(data), lm=locMap(data);
  const employees=a.employeeIds.map(id=>em.get(id)?.name).filter(Boolean).join(', ');
  const locations=a.locationIds.map(id=>lm.get(id)?.label).filter(Boolean).join(', ');
  const service=sm.get(a.serviceId)?.name||'Serviço';
  return {employees,locations,service};
}

function setTitle(title){ $('#pageTitle').textContent=title; $$('[data-route]').forEach(x=>x.classList.toggle('active',x.dataset.route===state.route)); }
function dateNavHTML(){
  const isToday=state.selectedDate===todayISO();
  return `<div class="date-nav card">
    <button class="icon-btn" data-action="date-prev" type="button" aria-label="Data anterior">‹</button>
    <button class="btn btn-secondary date-current" data-action="date-picker" type="button"><strong>${isToday?'Hoje':formatDate(state.selectedDate)}</strong><small>${esc(formatDate(state.selectedDate,true))}</small></button>
    <button class="icon-btn" data-action="date-next" type="button" aria-label="Próxima data" ${state.settings?.blockFutureDates&&state.selectedDate>=todayISO()?'disabled':''}>›</button>
  </div>`;
}

async function render(){
  await loadSettings();
  if(state.route==='today') return renderToday();
  if(state.route==='point') return renderPoint();
  if(state.route==='history') return renderHistory();
  if(state.route==='reports') return renderReports();
  if(state.route==='more') return renderMore();
  if(state.route==='employees') return renderEmployees();
  if(state.route==='services') return renderServices();
  if(state.route==='locations') return renderLocations();
  if(state.route==='settings') return renderSettings();
  if(state.route==='audit') return renderAudit();
}

async function renderToday(){
  setTitle(state.selectedDate===todayISO()?'Hoje':formatDate(state.selectedDate));
  const data=await getData();
  const active=data.employees.filter(e=>e.active&&!e._deleted);
  const day=data.appointments.filter(a=>a.date===state.selectedDate);
  const pointed=new Set(day.flatMap(a=>a.employeeIds));
  const missing=active.filter(e=>!pointed.has(e.id));
  const counts={finalizado:0,em_andamento:0,pendente:0,pausado:0}; day.forEach(a=>counts[a.status]=(counts[a.status]||0)+1);
  const yesterday=dateAdd(state.selectedDate,-1);
  const carry=data.appointments.filter(a=>a.date===yesterday&&a.status!=='finalizado').slice(0,5);
  const recent=[...day].sort((a,b)=>String(b.createdAt).localeCompare(String(a.createdAt))).slice(0,8);
  main().innerHTML=`<div class="stack field-day">
    ${dateNavHTML()}
    <section class="day-overview" aria-label="Resumo do dia">
      <div class="day-coverage"><strong>${pointed.size}<span> de ${active.length}</span></strong><p>funcionários do cadastro de campo com apontamento</p></div>
      <div class="day-overview-side"><strong>${day.length} ${day.length===1?'registro':'registros'}</strong>${day.length?`<span>${counts.finalizado} concluídos no campo${counts.pendente?` · ${counts.pendente} com pendência`:''}</span>`:''}</div>
      ${day.length?`<button class="btn btn-primary" data-action="go-point" type="button">Novo apontamento</button>`:''}
    </section>
    ${missing.length?`<button class="missing-action" data-action="show-missing" type="button"><span><strong>${missing.length} ${missing.length===1?'funcionário sem apontamento':'funcionários sem apontamento'}</strong><small>Ver cadastro de campo nesta data</small></span><span aria-hidden="true">›</span></button>`:`<div class="notice">Todos os funcionários ativos do campo possuem apontamento nesta data.</div>`}
    ${carry.length?`<section><h2 class="section-title">Em aberto do dia anterior</h2><div class="list">${carry.map(a=>{const l=recordLabel(a,data);return `<div class="list-item"><div class="item-main"><div class="item-title">${esc(l.employees)}</div><div class="item-sub">${esc(l.service)} · ${esc(l.locations)}</div></div><button class="btn btn-sm btn-secondary" data-action="continue-record" data-id="${a.id}">Continuar hoje</button></div>`}).join('')}</div></section>`:''}
    <section><div class="row-between section-heading"><h2 class="section-title">Registros do dia</h2>${day.length?`<button class="btn btn-sm btn-outline" data-action="go-reports">Ver relatório</button>`:''}</div>${recent.length?`<div class="stack">${recent.map(a=>recordHTML(a,data)).join('')}</div>`:`<div class="empty"><strong>Nenhum apontamento nesta data.</strong><p>Escolha um funcionário para registrar o serviço executado.</p><button class="btn btn-primary" data-action="go-point" type="button">Novo apontamento</button></div>`}</section>
  </div>`;
}

function recordHTML(a,data){
  const l=recordLabel(a,data); const service=data.services.find(s=>s.id===a.serviceId);
  return `<article class="record">
    <div class="record-head"><div class="item-main"><div class="record-title truncate">${esc(l.employees)}</div><div class="record-meta">${esc(l.locations)} · ${esc(l.service)}${a.quantity!=null?` · ${esc(roundQty(a.quantity))} ${esc(a.unit||service?.unit||'')}`:''}</div></div>${statusBadge(a.status)}</div>
    <div class="record-meta">${esc(a.startTime||'--:--')}${a.endTime?`–${esc(a.endTime)}`:''}${a.observation?` · ${esc(a.observation)}`:''}</div>
    <div class="record-actions">
      <button class="btn btn-sm btn-outline" data-action="edit-record" data-id="${a.id}">Editar</button>
      <button class="btn btn-sm btn-outline" data-action="duplicate-record" data-id="${a.id}">Duplicar</button>
      ${a.status!=='finalizado'?`<button class="btn btn-sm btn-success" data-action="finish-record" data-id="${a.id}">Concluir</button>`:''}
      <button class="btn btn-sm btn-danger" data-action="delete-record" data-id="${a.id}">Excluir</button>
    </div>
  </article>`;
}

async function renderPoint(){
  setTitle('Apontar'); const data=await getData(); const employees=data.employees.filter(x=>x.active&&!x._deleted).sort(sortName);
  const query=state.pointEmployeeQuery;
  const recentIds=[]; [...data.appointments].sort((a,b)=>String(b.createdAt).localeCompare(String(a.createdAt))).forEach(a=>a.employeeIds.forEach(id=>{if(!recentIds.includes(id))recentIds.push(id)}));
  const recent=recentIds.slice(0,5).map(id=>employees.find(e=>e.id===id)).filter(Boolean);
  main().innerHTML=`<div class="stack">
    <div class="notice">Data do apontamento: <strong>${state.selectedDate===todayISO()?'Hoje':formatDate(state.selectedDate)}</strong>. Ao tocar em um colaborador, o apontamento já é iniciado.</div>
    <div class="row-between"><div class="section-title">Funcionários</div><button class="btn btn-sm ${state.pointMultiMode?'btn-primary':'btn-outline'}" data-action="toggle-multi">${state.pointMultiMode?'Cancelar seleção':'Selecionar vários'}</button></div>
    <div class="search"><input id="employeeSearch" autocomplete="off" placeholder="Buscar funcionário" value="${esc(query)}" /></div>
    ${state.pointMultiMode?`<button class="btn btn-primary btn-block ${state.pointSelectedEmployees.size?'':'hidden'}" id="multiStartBtn" data-action="start-multi">Apontar ${state.pointSelectedEmployees.size} selecionado(s)</button>`:''}
    <div id="employeeResults">${employeeListHTML(query?employees.filter(x=>normalize(x.name).includes(normalize(query))):employees,query?[]:recent)}</div>
  </div>`;
  $('#employeeSearch')?.addEventListener('input',e=>{state.pointEmployeeQuery=e.target.value;const q=normalize(state.pointEmployeeQuery);const filtered=employees.filter(x=>normalize(x.name).includes(q)); $('#employeeResults').innerHTML=employeeListHTML(filtered,[]);});
}
function employeeListHTML(employees,recent=[]){
  const groups=new Map(); employees.forEach(e=>{const k=(normalize(e.name)[0]||'#').toUpperCase(); if(!groups.has(k))groups.set(k,[]); groups.get(k).push(e);});
  const selected=state.pointSelectedEmployees;
  const item=e=>`<button class="list-item clickable" data-action="choose-employee" data-id="${e.id}" type="button"><div class="avatar">${esc(initials(e.name))}</div><div class="item-main"><div class="item-title">${esc(e.name)}</div><div class="item-sub">${esc([e.role,e.team].filter(Boolean).join(' · ')||'Colaborador')}</div></div>${state.pointMultiMode?`<span class="badge ${selected.has(e.id)?'badge-success':'badge-muted'}">${selected.has(e.id)?'Selecionado':'Selecionar'}</span>`:'<span>›</span>'}</button>`;
  return `${recent.length?`<div class="section-title">Recentes</div><div class="list">${recent.map(item).join('')}</div><div class="section-title">Todos</div>`:''}<div class="list">${[...groups.keys()].sort().map(k=>`<div class="alpha-header">${k}</div>${groups.get(k).map(item).join('')}`).join('')}</div>`;
}

async function openPointModal(employeeIds, existing=null, duplicate=false){
  const data=await getData(); const em=empMap(data); const selectedEmployees=new Set(employeeIds.filter(id=>em.has(id))); const employees=employeeIds.map(id=>em.get(id)).filter(Boolean);
  const a=existing||{}; const selectedLocations=new Set(a.locationIds||[]); let selectedService=a.serviceId||''; let status=duplicate?'em_andamento':(a.status||'em_andamento');
  let pointStart = duplicate?formatTime():a.startTime||formatTime();
  const appointmentHistory=data.appointments.filter(x=>x.employeeIds?.some(id=>employeeIds.includes(id)));
  const recentServiceIds=frequencyOrder(appointmentHistory.flatMap(x=>x.serviceId?[x.serviceId]:[]));
  const recentLocationIds=frequencyOrder(appointmentHistory.flatMap(x=>x.locationIds||[]));
  const activeServices=data.services.filter(s=>(s.active&&!s._deleted)||s.id===a.serviceId).sort((x,y)=>{const ix=recentServiceIds.indexOf(x.id),iy=recentServiceIds.indexOf(y.id); if(ix>=0||iy>=0){if(ix<0)return 1;if(iy<0)return -1;if(ix!==iy)return ix-iy;} return x.name.localeCompare(y.name,'pt-BR');});
  const activeLocations=data.locations.filter(l=>(l.active&&!l._deleted)||selectedLocations.has(l.id));
  const body=document.createElement('div'); body.className='modal-backdrop'; body.innerHTML=`<div class="modal" role="dialog" aria-modal="true">
    <div class="modal-head"><div><div class="eyebrow">${existing&&!duplicate?'EDITAR APONTAMENTO':'NOVO APONTAMENTO'}</div><h2>Apontamento de campo</h2></div><button class="modal-close" data-modal-close>✕</button></div>
    <div class="stack">
      <div class="form-group"><label class="form-label" for="pointEmployeeSearch">Funcionários neste apontamento</label><div id="pointSelectedEmployees" class="selected-people"></div><div class="search"><input id="pointEmployeeSearch" type="search" placeholder="Buscar e adicionar funcionário" autocomplete="off"></div><div id="pointEmployeeResults" class="participant-list"></div><details class="quick-create"><summary>Cadastrar novo funcionário</summary><div class="stack"><input id="quickEmployeeName" class="input" placeholder="Nome do funcionário" maxlength="160"><input id="quickEmployeeRole" class="input" placeholder="Função (opcional)" maxlength="160"><input id="quickEmployeeTeam" class="input" placeholder="Equipe (opcional)" maxlength="160"><button id="quickEmployeeSave" class="btn btn-outline" type="button">Cadastrar e adicionar</button></div></details></div>
      <div class="row-between card"><div><div class="small muted">Data</div><div class="bold">${state.selectedDate===todayISO()?'Hoje':formatDate(state.selectedDate)}</div></div><div><div class="small muted">Início</div><div class="bold">${esc(pointStart)}</div></div></div>
      <div class="form-group"><label class="form-label">1. Local</label><div class="search"><input id="locSearch" placeholder="Buscar casa, bloco ou local" autocomplete="off"></div><small id="locResultCount" class="small muted"></small><div id="locRecent"></div><div id="locResults" class="choice-grid"></div></div>
      <div class="form-group"><label class="form-label">2. Serviço</label><div class="search"><input id="svcSearch" placeholder="Buscar serviço" autocomplete="off"></div><small id="svcResultCount" class="small muted"></small><div id="svcResults" class="choice-grid"></div></div>
      <div id="quantityBox"></div>
      <div class="form-group"><label class="form-label">3. Situação do serviço</label><div class="segmented" id="statusSeg"><button data-status="em_andamento">Em andamento</button><button data-status="finalizado">Concluído</button><button data-status="pendente">Com pendência</button><button data-status="pausado">Pausado</button></div></div>
      <div class="form-group"><label class="form-label">Observação (opcional)</label><div class="chip-row"><button class="chip" data-obs="Aguardando material">Aguardando material</button><button class="chip" data-obs="Aguardando qualidade">Aguardando qualidade</button><button class="chip" data-obs="Necessita correção">Necessita correção</button><button class="chip" data-obs="Retornar amanhã">Retornar amanhã</button></div><textarea id="observation" class="textarea" placeholder="Adicionar observação">${esc(a.observation||'')}</textarea></div>
      <button class="btn btn-primary btn-block" id="savePointBtn" type="button">${existing&&!duplicate?'Salvar alterações':'Salvar apontamento'}</button>
  </div></div>`;
  $('#modalRoot').appendChild(body);
  let dirty=false; let saved=false;
  const close=()=>{if(dirty&&!saved&&!confirm('Há dados preenchidos neste apontamento. Fechar e descartar?'))return;body.remove();};
  body.addEventListener('input',()=>{dirty=true;});
  body.addEventListener('change',()=>{dirty=true;});
  body.addEventListener('click',e=>{if(e.target===body||e.target.closest('[data-modal-close]'))close();});
  const renderPeople=()=>{const query=normalize($('#pointEmployeeSearch',body).value);const available=data.employees.filter(e=>!e._deleted&&(e.active||selectedEmployees.has(e.id))).sort(sortName);$('#pointSelectedEmployees',body).innerHTML=[...selectedEmployees].map(id=>em.get(id)).filter(Boolean).map(e=>`<span class="person-tag">${esc(e.name)} <button type="button" data-remove-employee="${esc(e.id)}" aria-label="Remover ${esc(e.name)}">×</button></span>`).join('')||'<span class="small muted">Selecione pelo menos um funcionário.</span>';$('#pointEmployeeResults',body).innerHTML=available.filter(e=>normalize(`${e.name} ${e.role||''} ${e.team||''}`).includes(query)).slice(0,30).map(e=>`<button class="participant-option ${selectedEmployees.has(e.id)?'selected':''}" type="button" data-point-employee="${esc(e.id)}" aria-pressed="${selectedEmployees.has(e.id)}"><span>${esc(e.name)}</span><small>${selectedEmployees.has(e.id)?'Adicionado':'Adicionar'}</small></button>`).join('')||'<div class="empty">Nenhum funcionário encontrado.</div>';};
  renderPeople();$('#pointEmployeeSearch',body).addEventListener('input',renderPeople);
  $('#quickEmployeeSave',body).addEventListener('click',async()=>{const name=$('#quickEmployeeName',body).value.trim(),role=$('#quickEmployeeRole',body).value.trim(),team=$('#quickEmployeeTeam',body).value.trim();if(!name)return toast('Informe o nome do funcionário');if(data.employees.some(e=>!e._deleted&&normalize(e.name)===normalize(name)))return toast('Funcionário já cadastrado. Use a busca para adicioná-lo.');const rec={id:uid(),name,role,team,active:true,updatedAt:nowISO()};await dbPut('employees',rec);await audit('criou','employee',rec.id,rec.name);data.employees.push(rec);em.set(rec.id,rec);selectedEmployees.add(rec.id);$('#quickEmployeeName',body).value='';$('#quickEmployeeRole',body).value='';$('#quickEmployeeTeam',body).value='';$('details.quick-create',body).open=false;renderPeople();toast('Funcionário cadastrado e adicionado');});
  const renderLoc=(q='')=>{ const nq=normalize(q); const all=activeLocations.filter(l=>normalize(l.label).includes(nq)); let list=all; if(!q){list.sort((x,y)=>{const ix=recentLocationIds.indexOf(x.id),iy=recentLocationIds.indexOf(y.id); if(ix>=0||iy>=0){if(ix<0)return 1;if(iy<0)return -1;if(ix!==iy)return ix-iy;} return x.label.localeCompare(y.label,'pt-BR',{numeric:true});});} list=list.slice(0,40); $('#locResultCount',body).textContent=all.length>40?`Mostrando 40 de ${all.length} locais`:`${all.length} local${all.length===1?'':'is'} encontrado${all.length===1?'':'s'}`; $('#locResults',body).innerHTML=list.map(l=>`<button class="choice ${selectedLocations.has(l.id)?'selected':''}" data-loc="${l.id}" type="button">${esc(l.label)}<small>${esc(l.type)}</small></button>`).join('')||'<div class="empty">Nenhum local encontrado.</div>'; };
  const renderQuantity=(value=a.quantity??1)=>{const s=activeServices.find(x=>x.id===selectedService); const box=$('#quantityBox',body); if(!s?.requiresQuantity){box.innerHTML='';return;} box.innerHTML=`<div class="form-group"><label class="form-label">Quantidade (${esc(s.unit||'un.')})</label><div class="stepper"><button data-qminus type="button">−</button><input id="quantity" class="input" inputmode="decimal" value="${esc(value)}"></div></div>`;};
  const renderSvc=(q='')=>{const previousQuantity=$('#quantity',body)?.value;const nq=normalize(q); const all=activeServices.filter(s=>normalize(s.name).includes(nq)); const list=all.slice(0,40); $('#svcResults',body).innerHTML=list.map(s=>`<button class="choice ${selectedService===s.id?'selected':''}" data-svc="${s.id}" type="button">${esc(s.name)}<small>${esc(s.category||s.unit||'')}</small></button>`).join('')||'<div class="empty">Nenhum serviço encontrado.</div>'; $('#svcResultCount',body).textContent=all.length>40?`Mostrando 40 de ${all.length} serviços`:`${all.length} serviço${all.length===1?'':'s'} encontrado${all.length===1?'':'s'}`;renderQuantity(previousQuantity??a.quantity??1);};
  renderLoc(); renderSvc();
  $$('#statusSeg button',body).forEach(b=>b.classList.toggle('active',b.dataset.status===status));
  $('#locSearch',body).addEventListener('input',e=>renderLoc(e.target.value)); $('#svcSearch',body).addEventListener('input',e=>renderSvc(e.target.value));
  body.addEventListener('click',async e=>{
    const person=e.target.closest('[data-point-employee]');if(person){const id=person.dataset.pointEmployee;selectedEmployees.has(id)?selectedEmployees.delete(id):selectedEmployees.add(id);dirty=true;renderPeople();return;}
    const remove=e.target.closest('[data-remove-employee]');if(remove){selectedEmployees.delete(remove.dataset.removeEmployee);dirty=true;renderPeople();return;}
    const loc=e.target.closest('[data-loc]'); if(loc){const id=loc.dataset.loc; selectedLocations.has(id)?selectedLocations.delete(id):selectedLocations.add(id);dirty=true; renderLoc($('#locSearch',body).value); return;}
    const svc=e.target.closest('[data-svc]'); if(svc){selectedService=svc.dataset.svc;renderQuantity(1);renderSvc($('#svcSearch',body).value);dirty=true;return;}
    const st=e.target.closest('[data-status]'); if(st){status=st.dataset.status;dirty=true; $$('#statusSeg button',body).forEach(b=>b.classList.toggle('active',b.dataset.status===status));return;}
    const ob=e.target.closest('[data-obs]'); if(ob){const t=$('#observation',body); t.value=t.value?`${t.value}; ${ob.dataset.obs}`:ob.dataset.obs;dirty=true;return;}
    if(e.target.closest('[data-qminus]')){const q=$('#quantity',body); q.value=Math.max(0,(Number(String(q.value).replace(',','.'))||0)-1);dirty=true;return;}
    if(e.target.closest('[data-qplus]')){const q=$('#quantity',body); q.value=(Number(String(q.value).replace(',','.'))||0)+1;dirty=true;return;}
  });
  $('#savePointBtn',body).addEventListener('click',async()=>{
    const saveButton=$('#savePointBtn',body); if(saveButton.disabled)return;
    if(selectedEmployees.size===0){toast('Adicione pelo menos um funcionário');return;}
    if(selectedLocations.size===0){toast('Selecione pelo menos um local');return;}
    if(!selectedService){toast('Selecione um serviço');return;}
    const service=activeServices.find(s=>s.id===selectedService); let quantity=null;
    if(service?.requiresQuantity){quantity=Number(String($('#quantity',body)?.value||'').replace(',','.')); if(!Number.isFinite(quantity)||quantity<0){toast('Informe uma quantidade válida');return;}}
    const rec={
      id:(existing&&!duplicate)?existing.id:uid(), date:state.selectedDate, startTime:pointStart,
      endTime:status==='finalizado'?(a.endTime||formatTime()):'', employeeIds:[...selectedEmployees], locationIds:[...selectedLocations], serviceId:selectedService,
      quantity, unit:service?.unit||'', status, observation:$('#observation',body).value.trim(),
      createdAt:(existing&&!duplicate)?existing.createdAt:nowISO(), updatedAt:nowISO()
    };
    saveButton.disabled=true;saveButton.textContent='Salvando…';
    try{
      await dbPut('appointments',rec); await audit(existing&&!duplicate?'editou':'criou','apontamento',rec.id,recordLabel(rec,data).service); scheduleSync(); saved=true; body.remove(); toast(existing&&!duplicate?'Apontamento atualizado':'Apontamento salvo'); state.pointSelectedEmployees.clear(); state.pointMultiMode=false; state.pointEmployeeQuery=''; await render();
    }catch(error){saveButton.disabled=false;saveButton.textContent=existing&&!duplicate?'Salvar alterações':'Salvar apontamento';toast(`Não foi possível salvar: ${error.message}`,6000);}
  });
}
function frequencyOrder(ids){const c=new Map(); ids.forEach(id=>c.set(id,(c.get(id)||0)+1)); return [...c.entries()].sort((a,b)=>b[1]-a[1]).map(x=>x[0]);}

async function renderHistory(){
  setTitle('Histórico'); const data=await getData();
  main().innerHTML=`<div class="stack">${dateNavHTML()}<div class="grid grid-2-md">
    <div class="search"><input id="histSearch" placeholder="Buscar funcionário, serviço ou local"></div>
    <select id="histStatus" class="select" aria-label="Filtrar por situação"><option value="">Todas as situações</option><option value="em_andamento">Em andamento</option><option value="finalizado">Concluído no campo</option><option value="pendente">Com pendência</option><option value="pausado">Pausado</option></select>
  </div><div id="historyList"></div></div>`;
  const update=()=>{const q=normalize($('#histSearch').value),st=$('#histStatus').value; const rows=data.appointments.filter(a=>a.date===state.selectedDate).filter(a=>!st||a.status===st).filter(a=>{const l=recordLabel(a,data); return !q||normalize(`${l.employees} ${l.locations} ${l.service} ${a.observation||''}`).includes(q)}).sort((a,b)=>String(b.createdAt).localeCompare(String(a.createdAt))); $('#historyList').innerHTML=rows.length?`<div class="stack">${rows.map(a=>recordHTML(a,data)).join('')}</div>`:'<div class="empty">Nenhum registro encontrado.</div>';}; update(); $('#histSearch').addEventListener('input',update); $('#histStatus').addEventListener('change',update);
}

async function reportData(){ const data=await getData(); const rows=data.appointments.filter(a=>a.date===state.selectedDate).sort((a,b)=>(a.startTime||'').localeCompare(b.startTime||'')); const active=data.employees.filter(e=>e.active&&!e._deleted); const pointed=new Set(rows.flatMap(a=>a.employeeIds)); return {data,rows,active,pointed,missing:active.filter(e=>!pointed.has(e.id))}; }
async function renderReports(){
  setTitle('Relatórios'); const {data,rows,active,pointed,missing}=await reportData(); const counts={finalizado:0,em_andamento:0,pendente:0,pausado:0}; rows.forEach(a=>counts[a.status]=(counts[a.status]||0)+1); missing.sort(sortName);
  main().innerHTML=`<div class="stack">${dateNavHTML()}
    <p class="section-context">Resumo do cadastro de campo para esta obra e data. Os números centrais podem diferir até os cadastros serem vinculados.</p>
    <div class="metrics"><div class="metric"><strong>${active.length}</strong><span>Funcionários ativos no campo</span></div><div class="metric"><strong>${pointed.size}</strong><span>Com apontamento</span></div><div class="metric"><strong>${missing.length}</strong><span>Sem apontamento</span></div><div class="metric"><strong>${rows.length}</strong><span>Registros no dia</span></div></div>
    <p class="status-summary">${counts.finalizado} concluídos no campo · ${counts.pendente} com pendência · ${counts.em_andamento} em andamento · ${counts.pausado} pausados</p>
    <div class="report-actions"><button class="btn btn-primary" data-action="export-xlsx">Baixar Excel</button><button class="btn btn-outline" data-action="export-pdf">Baixar PDF</button><button class="btn btn-outline" data-action="share-report">Compartilhar PDF</button></div>
    <section><h2 class="section-title">Prévia dos registros</h2>
    ${rows.length?`<div class="report-mobile-list">${rows.map(a=>{const l=recordLabel(a,data);return `<article class="report-mobile-row"><div class="row-between"><strong>${esc(l.employees)}</strong>${statusBadge(a.status)}</div><p>${esc(l.service)} · ${esc(l.locations)}</p><small>${esc(a.startTime||'Sem horário')}${a.quantity==null?'':` · ${esc(roundQty(a.quantity))} ${esc(a.unit||'')}`}</small>${a.observation?`<p class="report-observation">${esc(a.observation)}</p>`:''}</article>`}).join('')}</div><div class="table-wrap report-desktop-table"><table><thead><tr><th>Hora</th><th>Funcionário</th><th>Local</th><th>Serviço</th><th>Qtd.</th><th>Situação</th><th>Observação</th></tr></thead><tbody>${rows.map(a=>{const l=recordLabel(a,data);return `<tr><td>${esc(a.startTime||'')}</td><td>${esc(l.employees)}</td><td>${esc(l.locations)}</td><td>${esc(l.service)}</td><td>${a.quantity==null?'':`${esc(roundQty(a.quantity))} ${esc(a.unit||'')}`}</td><td>${esc(statusLabel(a.status))}</td><td>${esc(a.observation||'')}</td></tr>`}).join('')}</tbody></table></div>`:'<div class="empty">Sem registros nesta data.</div>'}</section>
    ${missing.length?`<details class="missing-panel"><summary>${missing.length} ${missing.length===1?'funcionário sem apontamento':'funcionários sem apontamento'}<span aria-hidden="true">⌄</span></summary><div class="missing-panel-body"><label class="form-label" for="missingSearch">Buscar no cadastro de campo</label><input id="missingSearch" class="input" type="search" autocomplete="off" placeholder="Nome ou função"><p id="missingFilterCount" class="small muted"></p><div id="missingReportList" class="missing-list"></div></div></details>`:''}
  </div>`;
  if(missing.length){
    const update=()=>{const q=normalize($('#missingSearch').value);const filtered=missing.filter(e=>normalize(`${e.name} ${e.role||''} ${e.team||''}`).includes(q));$('#missingFilterCount').textContent=`${filtered.length} ${filtered.length===1?'funcionário encontrado':'funcionários encontrados'}`;$('#missingReportList').innerHTML=filtered.map(e=>`<div class="missing-list-item"><strong>${esc(e.name)}</strong><small>${esc([e.role,e.team].filter(Boolean).join(' · ')||'Sem função ou equipe informada')}</small></div>`).join('')||'<div class="empty">Nenhum funcionário corresponde à busca.</div>';};
    update();$('#missingSearch').addEventListener('input',update);
  }
}

async function renderMore(){
  setTitle('Mais'); main().innerHTML=`<div class="stack">
    <button class="list-item clickable" data-route-go="employees"><div class="item-main"><div class="item-title">Funcionários</div><div class="item-sub">Cadastrar, editar e ativar/inativar</div></div><span>›</span></button>
    <button class="list-item clickable" data-route-go="services"><div class="item-main"><div class="item-title">Serviços</div><div class="item-sub">Serviços, categorias e unidades</div></div><span>›</span></button>
    <button class="list-item clickable" data-route-go="locations"><div class="item-main"><div class="item-title">Locais</div><div class="item-sub">Casas, blocos e áreas</div></div><span>›</span></button>
    <button class="list-item clickable" data-route-go="settings"><div class="item-main"><div class="item-title">Dados deste aparelho</div><div class="item-sub">Cópia local, sincronização e regras da obra</div></div><span>›</span></button>
    <button class="list-item clickable" data-route-go="audit"><div class="item-main"><div class="item-title">Auditoria</div><div class="item-sub">Histórico de alterações</div></div><span>›</span></button>
    <button class="list-item clickable" data-action="sync-now"><div class="item-main"><div class="item-title">Sincronizar agora</div><div class="item-sub">Com o servidor, quando disponível</div></div><span>↻</span></button>
  </div>`;
}

async function renderEmployees(){
  setTitle('Funcionários'); const rows=(await dbGetAll('employees')).filter(x=>!x._deleted).sort(sortName);
  main().innerHTML=`<div class="stack"><div class="catalog-actions"><button class="btn btn-primary" data-action="add-employee">＋ Novo funcionário</button><button class="btn btn-outline" data-action="catalog-import" data-kind="employees">Importar Excel</button><button class="btn btn-outline" data-action="catalog-template" data-kind="employees">Baixar modelo</button></div><input id="catalogFile" class="hidden" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Selecionar planilha de funcionários"><p class="section-context">Use o modelo Excel para incluir funcionários neste cadastro de campo. Registros existentes não são alterados.</p><div class="search"><input id="crudSearch" placeholder="Buscar funcionário"></div><div id="crudList" class="list"></div></div>`;
  const update=()=>{const q=normalize($('#crudSearch').value); const f=rows.filter(x=>normalize(`${x.name} ${x.role} ${x.team}`).includes(q)); $('#crudList').innerHTML=f.map(x=>`<div class="list-item"><div class="avatar">${esc(initials(x.name))}</div><div class="item-main"><div class="item-title">${esc(x.name)}</div><div class="item-sub">${esc([x.role,x.team].filter(Boolean).join(' · ')||'Sem função/equipe')} · ${x.active?'Ativo':'Inativo'}</div></div><button class="btn btn-sm btn-outline" data-action="edit-employee" data-id="${x.id}">Editar</button></div>`).join('')||'<div class="empty">Nenhum funcionário.</div>';}; update(); $('#crudSearch').addEventListener('input',update);
}
async function renderServices(){
  setTitle('Serviços'); const rows=(await dbGetAll('services')).filter(x=>!x._deleted).sort((a,b)=>a.name.localeCompare(b.name,'pt-BR'));
  main().innerHTML=`<div class="stack"><div class="catalog-actions"><button class="btn btn-primary" data-action="add-service">＋ Novo serviço</button><button class="btn btn-outline" data-action="catalog-import" data-kind="services">Importar Excel</button><button class="btn btn-outline" data-action="catalog-template" data-kind="services">Baixar modelo</button></div><input id="catalogFile" class="hidden" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Selecionar planilha de serviços"><p class="section-context">Use o modelo Excel para incluir serviços neste cadastro de campo. Registros existentes não são alterados.</p><div class="search"><input id="crudSearch" placeholder="Buscar serviço"></div><div id="crudList" class="list"></div></div>`;
  const update=()=>{const q=normalize($('#crudSearch').value); const f=rows.filter(x=>normalize(`${x.name} ${x.category} ${x.unit}`).includes(q)); $('#crudList').innerHTML=f.map(x=>`<div class="list-item"><div class="item-main"><div class="item-title">${esc(x.name)}</div><div class="item-sub">${esc(x.category||'Sem categoria')} · ${esc(x.unit||'un.')} · ${x.requiresQuantity?'Com quantidade':'Sem quantidade'} · ${x.active?'Ativo':'Inativo'}</div></div><button class="btn btn-sm btn-outline" data-action="edit-service" data-id="${x.id}">Editar</button></div>`).join('')||'<div class="empty">Nenhum serviço.</div>';}; update(); $('#crudSearch').addEventListener('input',update);
}
async function renderLocations(){
  setTitle('Locais'); const rows=(await dbGetAll('locations')).filter(x=>!x._deleted).sort((a,b)=>a.label.localeCompare(b.label,'pt-BR',{numeric:true}));
  main().innerHTML=`<div class="stack"><button class="btn btn-primary btn-block" data-action="add-location">＋ Novo local</button><div class="search"><input id="crudSearch" placeholder="Buscar local"></div><div id="crudList" class="list"></div></div>`;
  const update=()=>{const q=normalize($('#crudSearch').value); const f=rows.filter(x=>normalize(`${x.label} ${x.type}`).includes(q)).slice(0,200); $('#crudList').innerHTML=f.map(x=>`<div class="list-item"><div class="item-main"><div class="item-title">${esc(x.label)}</div><div class="item-sub">${esc(x.type)} · ${x.active?'Ativo':'Inativo'}</div></div><button class="btn btn-sm btn-outline" data-action="edit-location" data-id="${x.id}">Editar</button></div>`).join('')||'<div class="empty">Nenhum local.</div>';}; update(); $('#crudSearch').addEventListener('input',update);
}

async function openCrudModal(kind,id=null){
  const configs={employee:{store:'employees',title:'Funcionário'},service:{store:'services',title:'Serviço'},location:{store:'locations',title:'Local'}}; const c=configs[kind]; const old=id?await dbGet(c.store,id):null;
  let fields='';
  if(kind==='employee') fields=`<div class="form-group"><label class="form-label">Nome</label><input id="fName" class="input" value="${esc(old?.name||'')}"></div><div class="form-group"><label class="form-label">Função</label><input id="fRole" class="input" value="${esc(old?.role||'')}"></div><div class="form-group"><label class="form-label">Equipe</label><input id="fTeam" class="input" value="${esc(old?.team||'')}"></div>`;
  if(kind==='service') fields=`<div class="form-group"><label class="form-label">Nome</label><input id="fName" class="input" value="${esc(old?.name||'')}"></div><div class="form-group"><label class="form-label">Categoria</label><input id="fCategory" class="input" value="${esc(old?.category||'')}"></div><div class="form-group"><label class="form-label">Unidade</label><input id="fUnit" class="input" value="${esc(old?.unit||'un.')}"></div><label class="row card"><input id="fRequires" type="checkbox" ${old?.requiresQuantity?'checked':''}><span>Exigir quantidade no apontamento</span></label>`;
  if(kind==='location') fields=`<div class="form-group"><label class="form-label">Nome/identificação</label><input id="fLabel" class="input" value="${esc(old?.label||'')}"></div><div class="form-group"><label class="form-label">Tipo</label><input id="fType" class="input" value="${esc(old?.type||'Outro')}"></div>`;
  const m=document.createElement('div');m.className='modal-backdrop';m.innerHTML=`<div class="modal"><div class="modal-head"><h2>${old?'Editar':'Novo'} ${c.title.toLowerCase()}</h2><button class="modal-close" data-modal-close>✕</button></div><div class="stack">${fields}<label class="row card"><input id="fActive" type="checkbox" ${old?.active!==false?'checked':''}><span>Ativo</span></label><button class="btn btn-primary btn-block" id="crudSave">Salvar</button>${old?`<button class="btn btn-danger btn-block" id="crudDelete">Excluir</button>`:''}</div></div>`;$('#modalRoot').appendChild(m);const close=()=>m.remove();m.addEventListener('click',e=>{if(e.target===m||e.target.closest('[data-modal-close]'))close();});
  $('#crudSave',m).onclick=async()=>{let rec={...(old||{}),id:old?.id||uid(),active:$('#fActive',m).checked,updatedAt:nowISO()}; if(kind==='employee'){rec.name=$('#fName',m).value.trim();rec.role=$('#fRole',m).value.trim();rec.team=$('#fTeam',m).value.trim(); if(!rec.name)return toast('Informe o nome');} if(kind==='service'){rec.name=$('#fName',m).value.trim();rec.category=$('#fCategory',m).value.trim();rec.unit=$('#fUnit',m).value.trim()||'un.';rec.requiresQuantity=$('#fRequires',m).checked;if(!rec.name)return toast('Informe o serviço');} if(kind==='location'){rec.label=$('#fLabel',m).value.trim();rec.type=$('#fType',m).value.trim()||'Outro';if(!rec.label)return toast('Informe o local');} await dbPut(c.store,rec);await audit(old?'editou':'criou',kind,rec.id,rec.name||rec.label);scheduleSync();close();toast('Salvo');render();};
  if(old) $('#crudDelete',m).onclick=async()=>{if(!confirm(`Excluir ${c.title.toLowerCase()}?`))return; old._deleted=true;old.updatedAt=nowISO();await dbPut(c.store,old);await audit('excluiu',kind,old.id,old.name||old.label);scheduleSync();close();toast('Excluído');render();};
}

async function renderSettings(){
  setTitle('Dados deste aparelho'); const s=state.settings;
  main().innerHTML=`<div class="stack settings-page">
    <section class="card stack"><div><h2 class="section-title">Configurações da obra</h2><p class="section-context">Definidas no sistema O gestor de Campo e sincronizadas com este aparelho.</p></div>
      <dl class="settings-readonly"><div><dt>Empresa</dt><dd>${esc(s.companyName||'Não informada')}</dd></div><div><dt>Obra</dt><dd>${esc(s.workspaceName||state.workName||'Não informada')}</dd></div><div><dt>Responsável pelo apontamento</dt><dd>${esc(s.responsible||'Não informado')}</dd></div><div><dt>Datas futuras</dt><dd>${s.blockFutureDates!==false?'Bloqueadas':'Permitidas'}</dd></div></dl>
      <p class="small muted">Na importação para produção, a quantidade coletiva é dividida igualmente entre os funcionários. Medição e pagamento seguem seus próprios fluxos.</p>
      <p class="small muted">As configurações administrativas e os cadastros centrais são mantidos no Gestor de Campo. As alterações feitas aqui sincronizam com o mesmo sistema.</p>
    </section>
    <section class="card stack"><div><h2 class="section-title">Cópia local da obra</h2><p class="section-context">Os dados deste aplicativo ficam disponíveis offline e sincronizam quando há conexão.</p></div>
      <div class="report-actions"><button class="btn btn-outline" data-action="backup-export">Baixar cópia JSON</button><button class="btn btn-outline" data-action="backup-import">Importar cópia JSON</button></div>
      <input id="backupFile" class="hidden" type="file" accept="application/json" aria-label="Selecionar cópia JSON para importar">
      <p class="small muted">Este arquivo contém somente os dados do campo desta obra. O backup completo do sistema O gestor de Campo está nas configurações.</p>
      <hr><button class="btn btn-danger" data-action="reset-data">Limpar cópia e recarregar</button>
    </section>
  </div>`;
}
function auditLabel(row){
  const actions={criou:'Criou',editou:'Editou',excluiu:'Excluiu',finalizou:'Concluiu',continuou:'Continuou'};
  const entities={apontamento:'apontamento',employee:'funcionário',service:'serviço',location:'local',configuracoes:'configurações'};
  return `${actions[row.action]||String(row.action||'Ação').replaceAll('_',' ')} ${entities[row.entity]||String(row.entity||'registro').replaceAll('_',' ')}`;
}
async function renderAudit(){
  setTitle('Auditoria'); const rows=(await dbGetAll('audit')).filter(x=>!x._deleted).sort((a,b)=>String(b.at).localeCompare(String(a.at))).slice(0,300);
  main().innerHTML=rows.length?`<div class="stack"><p class="section-context">Últimas ${rows.length} alterações registradas neste aparelho.</p><div class="list">${rows.map(x=>`<div class="list-item audit-item"><div class="item-main"><div class="item-title">${esc(auditLabel(x))}</div><div class="item-sub">${x.details?`${esc(x.details)} · `:''}${esc(new Date(x.at).toLocaleString('pt-BR'))}</div><div class="tiny muted">Registro ${esc(x.entityId||'—')}</div></div></div>`).join('')}</div></div>`:'<div class="empty">Nenhuma alteração registrada neste aparelho.</div>';
}

async function showMissing(){
  const data=await getData();const day=data.appointments.filter(a=>a.date===state.selectedDate);const set=new Set(day.flatMap(a=>a.employeeIds));const rows=data.employees.filter(e=>e.active&&!e._deleted&&!set.has(e.id)).sort(sortName);
  const m=document.createElement('div');m.className='modal-backdrop';m.innerHTML=`<div class="modal"><div class="modal-head"><h2>Sem apontamento · cadastro de campo</h2><button class="modal-close" data-modal-close aria-label="Fechar">✕</button></div><div class="stack"><div class="search"><input id="missingModalSearch" type="search" autocomplete="off" aria-label="Buscar funcionário sem apontamento" placeholder="Buscar funcionário"></div><p id="missingModalCount" class="small muted"></p><div id="missingModalList" class="list"></div></div></div>`;$('#modalRoot').appendChild(m);
  const update=()=>{const q=normalize($('#missingModalSearch',m).value);const filtered=rows.filter(e=>normalize(`${e.name} ${e.role||''} ${e.team||''}`).includes(q));$('#missingModalCount',m).textContent=`${filtered.length} ${filtered.length===1?'funcionário':'funcionários'} sem apontamento`;$('#missingModalList',m).innerHTML=filtered.map(e=>`<button class="list-item clickable" data-missing-id="${e.id}" type="button"><div class="avatar">${esc(initials(e.name))}</div><div class="item-main"><div class="item-title">${esc(e.name)}</div><div class="item-sub">${esc([e.role,e.team].filter(Boolean).join(' · ')||'Sem função ou equipe informada')}</div></div><span>Apontar ›</span></button>`).join('')||'<div class="empty">Nenhum funcionário corresponde à busca.</div>';};
  update();$('#missingModalSearch',m).addEventListener('input',update);
  m.addEventListener('click',e=>{if(e.target===m||e.target.closest('[data-modal-close]'))m.remove();const b=e.target.closest('[data-missing-id]');if(b){m.remove();openPointModal([b.dataset.missingId]);}});
}

async function finishRecord(id){const a=await dbGet('appointments',id);if(!a)return;a.status='finalizado';a.endTime=formatTime();a.updatedAt=nowISO();await dbPut('appointments',a);await audit('finalizou','apontamento',id);scheduleSync();toast('Apontamento concluído no campo');render();}
async function deleteRecord(id){if(!confirm('Excluir este apontamento?'))return;const a=await dbGet('appointments',id);if(!a)return;a._deleted=true;a.updatedAt=nowISO();await dbPut('appointments',a);await audit('excluiu','apontamento',id);scheduleSync();toast('Apontamento excluído');render();}
async function continueRecord(id){const a=await dbGet('appointments',id);if(!a)return;const rec={...a,id:uid(),date:state.selectedDate,startTime:formatTime(),endTime:'',status:'em_andamento',createdAt:nowISO(),updatedAt:nowISO(),_deleted:false};await dbPut('appointments',rec);await audit('continuou','apontamento',rec.id);scheduleSync();toast('Serviço continuado hoje');render();}

function xmlEscape(v){return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');}
function colName(n){let s='';for(;n>0;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
function sheetXML(rows){return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rows.map((row,ri)=>`<row r="${ri+1}">${row.map((v,ci)=>{const ref=`${colName(ci+1)}${ri+1}`;if(typeof v==='number'&&Number.isFinite(v))return `<c r="${ref}"><v>${v}</v></c>`;return `<c r="${ref}" t="inlineStr"><is><t>${xmlEscape(v)}</t></is></c>`}).join('')}</row>`).join('')}</sheetData></worksheet>`;}
const crcTable=(()=>{const t=[];for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;t[n]=c>>>0;}return t;})();
function crc32(bytes){let c=0xffffffff;for(const b of bytes)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0;}
function u16(n){return [n&255,(n>>>8)&255]} function u32(n){return [n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255]}
function zipStore(files){const enc=new TextEncoder();const chunks=[];const central=[];let offset=0;for(const f of files){const name=enc.encode(f.name),data=typeof f.data==='string'?enc.encode(f.data):f.data,crc=crc32(data);const local=new Uint8Array([0x50,0x4b,0x03,0x04,...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(data.length),...u32(data.length),...u16(name.length),...u16(0)]);chunks.push(local,name,data);const cent=new Uint8Array([0x50,0x4b,0x01,0x02,...u16(20),...u16(20),...u16(0),...u16(0),...u16(0),...u16(0),...u32(crc),...u32(data.length),...u32(data.length),...u16(name.length),...u16(0),...u16(0),...u16(0),...u16(0),...u32(0),...u32(offset)]);central.push(cent,name);offset+=local.length+name.length+data.length;}const centralSize=central.reduce((n,x)=>n+x.length,0),centralOffset=offset;const end=new Uint8Array([0x50,0x4b,0x05,0x06,...u16(0),...u16(0),...u16(files.length),...u16(files.length),...u32(centralSize),...u32(centralOffset),...u16(0)]);return new Blob([...chunks,...central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});}
function catalogTemplateBlob(kind){
  const headers=kind==='employees'?['Nome','Função','Equipe','Ativo']:['Nome','Categoria','Unidade','Exigir quantidade','Ativo'];
  const title=kind==='employees'?'Funcionários':'Serviços';
  return zipStore([
    {name:'[Content_Types].xml',data:'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'},
    {name:'_rels/.rels',data:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'},
    {name:'xl/workbook.xml',data:`<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${title}" sheetId="1" r:id="rId1"/></sheets></workbook>`},
    {name:'xl/_rels/workbook.xml.rels',data:'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'},
    {name:'xl/worksheets/sheet1.xml',data:sheetXML([headers])}
  ]);
}
function catalogPreview(rows,current){const known=new Set(current.filter(x=>!x._deleted).map(x=>normalize(x.name.trim()))),seen=new Set(),add=[],skipped=[];for(const row of rows){const key=normalize(row.name.trim());if(known.has(key)||seen.has(key)){skipped.push(row.name);continue;}seen.add(key);add.push(row);}return {add,skipped};}
async function importCatalogFile(file,kind){
  try{
    if(!file.name.toLowerCase().endsWith('.xlsx')||file.size>4*1024*1024)throw new Error('Selecione um arquivo .xlsx de até 4 MB');
    if(!navigator.onLine)throw new Error('Conecte-se ao sistema O gestor de Campo para ler a planilha Excel');
    const auth=await fieldRequest('/api/auth/me');
    const result=await fieldRequest(`/api/apontamento/catalog-preview?obra_id=${WORK_ID}&kind=${kind}`,{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','X-CSRF-Token':auth.csrf_token},body:file});
    const {add,skipped}=catalogPreview(result.rows,await dbGetAll(kind));
    const m=document.createElement('div');m.className='modal-backdrop';
    m.innerHTML=`<div class="modal" role="dialog" aria-modal="true"><div class="modal-head"><h2>Conferir importação</h2><button class="modal-close" data-modal-close aria-label="Fechar">✕</button></div><div class="stack"><p class="section-context">${add.length} novo(s) ${kind==='employees'?'funcionário(s)':'serviço(s)'}; ${skipped.length} nome(s) já cadastrado(s) ou repetido(s) serão ignorados.</p><div class="import-preview">${add.slice(0,12).map(x=>`<div>${esc(x.name)} <small>${esc(kind==='employees'?x.role:x.category)}</small></div>`).join('')||'<div>Nenhum novo cadastro.</div>'}</div>${add.length>12?`<p class="small muted">Mostrando os primeiros 12 de ${add.length} novos cadastros.</p>`:''}<p class="small muted">A importação não modifica funcionários, serviços ou apontamentos já salvos.</p><button id="confirmCatalogImport" class="btn btn-primary btn-block" type="button" ${add.length?'':'disabled'}>Importar ${add.length} novo(s)</button></div></div>`;
    $('#modalRoot').appendChild(m);
    m.addEventListener('click',e=>{if(e.target===m||e.target.closest('[data-modal-close]'))m.remove();});
    $('#confirmCatalogImport',m).addEventListener('click',async()=>{try{const latest=catalogPreview(result.rows,await dbGetAll(kind));if(!latest.add.length){m.remove();toast('Nenhum cadastro novo para importar');return;}await dbImportCatalog(kind,latest.add);scheduleSync();m.remove();toast(`${latest.add.length} cadastro(s) importado(s)`);await render();}catch(error){toast(`Importação não concluída: ${error.message}`,6000);}});
  }catch(e){toast(`Importação não concluída: ${e.message}`,6000);}
}
async function makeXlsxBlob(){
  const {data,rows,active,pointed,missing}=await reportData();const detail=[['Data','Funcionário','Função','Equipe','Local','Serviço','Quantidade','Unidade','Início','Fim','Status','Observação']];const em=empMap(data),sm=svcMap(data),lm=locMap(data);for(const a of rows){for(const eid of a.employeeIds){const e=em.get(eid);detail.push([formatDate(a.date),e?.name||'',e?.role||'',e?.team||'',a.locationIds.map(id=>lm.get(id)?.label).filter(Boolean).join(', '),sm.get(a.serviceId)?.name||'',a.quantity??'',a.unit||'',a.startTime||'',a.endTime||'',statusLabel(a.status),a.observation||'']);}}
  const counts={};rows.forEach(a=>counts[a.status]=(counts[a.status]||0)+1);const summary=[['Relatório de apontamento de campo'],['Data',formatDate(state.selectedDate)],['Empresa',state.settings.companyName||''],['Obra',state.settings.workspaceName||''],['Responsável',state.settings.responsible||''],[],['Indicador','Valor'],['Funcionários ativos no campo',active.length],['Funcionários com apontamento',pointed.size],['Sem apontamento',missing.length],['Registros no dia',rows.length],['Concluídos no campo',counts.finalizado||0],['Em andamento',counts.em_andamento||0],['Com pendência',counts.pendente||0],['Pausados',counts.pausado||0]];const missingRows=[['Funcionário','Função','Equipe'],...missing.map(e=>[e.name,e.role||'',e.team||''])];
  const files=[
    {name:'[Content_Types].xml',data:`<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`},
    {name:'_rels/.rels',data:`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`},
    {name:'xl/workbook.xml',data:`<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Apontamentos" sheetId="1" r:id="rId1"/><sheet name="Resumo" sheetId="2" r:id="rId2"/><sheet name="Sem apontamento" sheetId="3" r:id="rId3"/></sheets></workbook>`},
    {name:'xl/_rels/workbook.xml.rels',data:`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/></Relationships>`},
    {name:'xl/worksheets/sheet1.xml',data:sheetXML(detail)},{name:'xl/worksheets/sheet2.xml',data:sheetXML(summary)},{name:'xl/worksheets/sheet3.xml',data:sheetXML(missingRows)}
  ];return zipStore(files);
}

function pdfEscape(s){return String(s??'').replace(/\\/g,'\\\\').replace(/\(/g,'\\(').replace(/\)/g,'\\)');}
function latin1Bytes(s){const arr=new Uint8Array(s.length);for(let i=0;i<s.length;i++){let c=s.charCodeAt(i);if(c>255)c=63;arr[i]=c;}return arr;}
function truncate(s,n){s=String(s??'').replace(/\s+/g,' ').trim();return s.length>n?s.slice(0,n-1)+'…':s;}
async function makePdfBlob(){
  const {data,rows,active,pointed,missing}=await reportData();const lines=[];const settings=state.settings;lines.push('APONTAMENTO DE CAMPO - RELATÓRIO DIÁRIO');lines.push(`Data: ${formatDate(state.selectedDate)}`);if(settings.companyName)lines.push(`Empresa: ${settings.companyName}`);if(settings.workspaceName)lines.push(`Obra: ${settings.workspaceName}`);if(settings.responsible)lines.push(`Responsável: ${settings.responsible}`);lines.push('');lines.push(`Ativos no campo: ${active.length} | Com apontamento: ${pointed.size} | Sem apontamento: ${missing.length} | Registros: ${rows.length}`);lines.push('');lines.push('HORA  FUNCIONÁRIO               LOCAL               SERVIÇO                   QTD       SITUAÇÃO');lines.push('-'.repeat(105));
  for(const a of rows){const l=recordLabel(a,data);const qty=a.quantity==null?'':`${roundQty(a.quantity)} ${a.unit||''}`;lines.push(`${(a.startTime||'').padEnd(5)} ${truncate(l.employees,25).padEnd(25)} ${truncate(l.locations,19).padEnd(19)} ${truncate(l.service,24).padEnd(24)} ${truncate(qty,9).padEnd(9)} ${truncate(statusLabel(a.status),18)}`);if(a.observation)lines.push(`      Obs: ${truncate(a.observation,82)}`);}
  if(missing.length){lines.push('');lines.push('SEM APONTAMENTO:');missing.forEach(e=>lines.push(`- ${e.name}${e.role?` (${e.role})`:''}`));}
  const perPage=66,pages=[];for(let i=0;i<lines.length;i+=perPage)pages.push(lines.slice(i,i+perPage));if(!pages.length)pages.push(['APONTAMENTO DE CAMPO - RELATÓRIO DIÁRIO','Sem registros.']);
  const objs=[];const pageRefs=[];for(let i=0;i<pages.length;i++)pageRefs.push(`${4+i*2} 0 R`);objs[1]='<< /Type /Catalog /Pages 2 0 R >>';objs[2]=`<< /Type /Pages /Kids [${pageRefs.join(' ')}] /Count ${pages.length} >>`;objs[3]='<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>';
  pages.forEach((page,i)=>{const p=4+i*2,s=5+i*2;let y=808;const content=page.map((line,idx)=>{const size=idx===0?12:8;const cmd=`BT /F1 ${size} Tf 32 ${y} Td (${pdfEscape(line)}) Tj ET\n`;y-=idx===0?18:11;return cmd;}).join('');const len=latin1Bytes(content).length;objs[p]=`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${s} 0 R >>`;objs[s]=`<< /Length ${len} >>\nstream\n${content}endstream`;});
  let pdf='%PDF-1.4\n%âãÏÓ\n';const offsets=[0];for(let i=1;i<objs.length;i++){offsets[i]=latin1Bytes(pdf).length;pdf+=`${i} 0 obj\n${objs[i]}\nendobj\n`;}const xref=latin1Bytes(pdf).length;pdf+=`xref\n0 ${objs.length}\n0000000000 65535 f \n`;for(let i=1;i<objs.length;i++)pdf+=`${String(offsets[i]).padStart(10,'0')} 00000 n \n`;pdf+=`trailer\n<< /Size ${objs.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return new Blob([latin1Bytes(pdf)],{type:'application/pdf'});
}

async function exportXlsx(){const b=await makeXlsxBlob();downloadBlob(b,`apontamento-${state.selectedDate}.xlsx`);toast('Excel gerado');}
async function exportPdf(){const b=await makePdfBlob();downloadBlob(b,`apontamento-${state.selectedDate}.pdf`);toast('PDF gerado');}
async function shareReport(){const b=await makePdfBlob();const file=new File([b],`apontamento-${state.selectedDate}.pdf`,{type:'application/pdf'});if(navigator.share&&navigator.canShare?.({files:[file]})){try{await navigator.share({title:'Relatório de Apontamento',text:`Relatório de ${formatDate(state.selectedDate)}`,files:[file]});}catch{}}else{downloadBlob(b,file.name);toast('PDF salvo para compartilhar');}}

async function exportBackup(){const payload={version:1,obraId:WORK_ID,exportedAt:nowISO(),data:await buildLocalSnapshot()};downloadBlob(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),`backup-apontamento-obra-${WORK_ID}-${todayISO()}.json`);toast('Backup exportado');}
async function importBackup(file){
  try{
    if(file.size>32*1024*1024) throw new Error('Backup excede 32 MB');
    const obj=JSON.parse(await file.text());
    if(obj.version!==1||!obj.data) throw new Error('Formato de backup inválido');
    if(obj.obraId!=null&&Number(obj.obraId)!==WORK_ID) throw new Error('Este backup pertence a outra obra');
    validateSnapshot(obj.data);
    await validateBackupRelations(obj.data);
    if(!confirm(`Importar o backup na obra ${state.workName}? Registros mais recentes serão preservados.${obj.obraId==null?' O arquivo antigo não identifica a obra: confira a origem.':''}`)) return;
    await applyRemoteSnapshot(obj.data);await loadSettings();scheduleSync();toast('Backup importado');await render();
  }catch(e){toast(`Backup não importado: ${e.message}`,6000);}
}
async function resetData(){
  if(!await syncNow(false)){toast('Sincronize os dados antes de limpar este aparelho.',5000);return;}
  if(!confirm('Limpar a cópia local e recarregá-la do servidor? Os dados do banco central serão preservados.'))return;
  await new Promise((resolve,reject)=>{const t=state.db.transaction(STORES,'readwrite');for(const s of STORES)t.objectStore(s).clear();t.oncomplete=resolve;t.onabort=t.onerror=()=>reject(t.error);});
  localStorage.removeItem(ACCESS_KEY);location.reload();
}

function openDatePicker(){const m=document.createElement('div');m.className='modal-backdrop';m.innerHTML=`<div class="modal"><div class="modal-head"><h2>Selecionar data</h2><button class="modal-close" data-modal-close>✕</button></div><div class="stack"><input id="pickDate" class="input" type="date" value="${state.selectedDate}" ${state.settings?.blockFutureDates?`max="${todayISO()}"`:''}><button class="btn btn-primary" id="pickDateOk">Usar esta data</button><button class="btn btn-secondary" id="pickToday">Hoje</button></div></div>`;$('#modalRoot').appendChild(m);const close=()=>m.remove();m.addEventListener('click',e=>{if(e.target===m||e.target.closest('[data-modal-close]'))close();});$('#pickDateOk',m).onclick=()=>{const v=$('#pickDate',m).value;if(v){state.selectedDate=v;close();render();}};$('#pickToday',m).onclick=()=>{state.selectedDate=todayISO();close();render();};}

async function handleAction(el){
  const action=el.dataset.action;if(!action)return;
  if(action==='date-prev'){state.selectedDate=dateAdd(state.selectedDate,-1);return render();}
  if(action==='date-next'){const n=dateAdd(state.selectedDate,1);if(state.settings?.blockFutureDates&&isFuture(n))return;state.selectedDate=n;return render();}
  if(action==='date-picker')return openDatePicker();
  if(action==='go-point'){state.route='point';return render();} if(action==='go-reports'){state.route='reports';return render();}
  if(action==='show-missing')return showMissing();
  if(action==='toggle-multi'){state.pointMultiMode=!state.pointMultiMode;if(!state.pointMultiMode){state.pointSelectedEmployees.clear();state.pointEmployeeQuery='';}return renderPoint();}
  if(action==='choose-employee'){if(state.pointMultiMode){state.pointSelectedEmployees.has(el.dataset.id)?state.pointSelectedEmployees.delete(el.dataset.id):state.pointSelectedEmployees.add(el.dataset.id);return renderPoint();}return openPointModal([el.dataset.id]);}
  if(action==='start-multi'){if(state.pointSelectedEmployees.size)return openPointModal([...state.pointSelectedEmployees]);}
  if(action==='edit-record'){const a=await dbGet('appointments',el.dataset.id);if(a)return openPointModal(a.employeeIds,a,false);}
  if(action==='duplicate-record'){const a=await dbGet('appointments',el.dataset.id);if(a)return openPointModal(a.employeeIds,a,true);}
  if(action==='finish-record')return finishRecord(el.dataset.id); if(action==='delete-record')return deleteRecord(el.dataset.id); if(action==='continue-record')return continueRecord(el.dataset.id);
  if(action==='export-xlsx')return exportXlsx(); if(action==='export-pdf')return exportPdf(); if(action==='share-report')return shareReport();
  if(action==='catalog-template'){const kind=el.dataset.kind;return downloadBlob(catalogTemplateBlob(kind),`modelo-${kind==='employees'?'funcionarios':'servicos'}-campo.xlsx`);}
  if(action==='catalog-import'){const input=$('#catalogFile');input.dataset.kind=el.dataset.kind;input.click();return;}
  if(action==='add-employee')return openCrudModal('employee'); if(action==='edit-employee')return openCrudModal('employee',el.dataset.id);
  if(action==='add-service')return openCrudModal('service'); if(action==='edit-service')return openCrudModal('service',el.dataset.id);
  if(action==='add-location')return openCrudModal('location'); if(action==='edit-location')return openCrudModal('location',el.dataset.id);
  if(action==='save-settings'){const s={...state.settings,companyName:$('#setCompany').value.trim(),workspaceName:$('#setWorkspace').value.trim(),responsible:$('#setResponsible').value.trim(),accent:'#167a62',blockFutureDates:$('#setBlockFuture').checked,updatedAt:nowISO()};await dbPut('settings',s);await audit('editou','configuracoes','app');state.settings=s;scheduleSync();toast('Configurações salvas');return render();}
  if(action==='backup-export')return exportBackup(); if(action==='backup-import')return $('#backupFile').click(); if(action==='reset-data')return resetData(); if(action==='sync-now')return syncNow(false);
}

document.addEventListener('click',async e=>{
  if(!state.db) return;
  try{
  const routeBtn=e.target.closest('[data-route]'); if(routeBtn){state.route=routeBtn.dataset.route;await render();return;}
  const go=e.target.closest('[data-route-go]'); if(go){state.route=go.dataset.routeGo;await render();return;}
  const action=e.target.closest('[data-action]'); if(action)await handleAction(action);
  }catch(error){console.error(error);toast(`Não foi possível concluir: ${error.message}`,6000);}
});
document.addEventListener('change',e=>{if(e.target.id==='backupFile'&&e.target.files?.[0])importBackup(e.target.files[0]);if(e.target.id==='catalogFile'&&e.target.files?.[0]){importCatalogFile(e.target.files[0],e.target.dataset.kind);e.target.value='';}});
window.addEventListener('online',()=>{if(state.db)syncNow(true).then(()=>render()).catch(error=>toast(error.message));});window.addEventListener('offline',()=>{state.syncState='offline';updateConnectionBadge();});
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();state.deferredInstall=e;$('#installBtn').classList.remove('hidden');});
$('#installBtn').addEventListener('click',async()=>{if(!state.deferredInstall)return;state.deferredInstall.prompt();await state.deferredInstall.userChoice;state.deferredInstall=null;$('#installBtn').classList.add('hidden');});

async function init(){
  if(location.protocol==='file:')return;
  $('#workSelector').addEventListener('change',()=>{
    const next=Number($('#workSelector').value);
    if(!Number.isSafeInteger(next)||next<=0||next===WORK_ID)return;
    localStorage.setItem('pms_field_work',String(next));
    location.replace(`/campo/index.html?obra_id=${next}`);
  });
  let auth;
  try{auth=await fetch('/api/auth/me',{cache:'no-store',signal:AbortSignal.timeout(12000)});}
  catch(error){
    let cached;try{cached=JSON.parse(localStorage.getItem(ACCESS_KEY));}catch{}
    if(!cached||!(cached.expiresAt>Date.now()))throw new Error('Conecte-se e entre no sistema O gestor de Campo para autorizar o acesso offline deste aparelho.');
    state.currentUser=cached.user;state.workName=cached.workName;state.syncState='offline';
  }
  if(auth){
  if(auth.status===401){localStorage.removeItem(ACCESS_KEY);sessionStorage.setItem('pms_return_to',location.pathname+location.search);location.replace('/login.html');return;}
  if(!auth.ok)throw new Error('Não foi possível validar a sessão do sistema O gestor de Campo.');
  const authData=await auth.json(); state.currentUser=authData.user; sessionStorage.setItem('pms_csrf',authData.csrf_token);
  if(authData.user.deve_trocar_senha){sessionStorage.setItem('pms_return_to',location.pathname+location.search);location.replace('/');return;}
  if(!authData.user.permissoes?.includes('*')&&!authData.user.permissoes?.includes('lancamentos')){localStorage.removeItem(ACCESS_KEY);main().innerHTML='<div class="danger-notice">Seu perfil não possui acesso ao aplicativo de apontamento. Solicite ao administrador a liberação da permissão de lançamentos.</div>';return;}
  const worksResponse=await fetch('/api/works',{cache:'no-store'}); if(!worksResponse.ok)throw new Error('Não foi possível carregar as obras autorizadas.');
  const works=await worksResponse.json();
  localStorage.setItem(WORKS_CACHE_KEY,JSON.stringify(works.map(({id,nome})=>({id:Number(id),nome:String(nome)}))));
  const accessExpiresAt=Date.parse(authData.expires_at);
  for(const allowedWork of works){
    localStorage.setItem(`pms_field_access_${Number(allowedWork.id)}`,JSON.stringify({user:authData.user,workName:String(allowedWork.nome),expiresAt:accessExpiresAt}));
  }
  let work=works.find(item=>Number(item.id)===WORK_ID);
  if(!work){
    const explicitlyRequested=new URLSearchParams(location.search).has('obra_id');
    if(!explicitlyRequested&&works.length){const first=works[0];localStorage.setItem('pms_field_work',String(first.id));location.replace(`/campo/index.html?obra_id=${Number(first.id)}`);return;}
    localStorage.removeItem(ACCESS_KEY);main().innerHTML='<div class="danger-notice">Sua conta não tem acesso à obra selecionada. Verifique as obras autorizadas com o administrador.</div>';return;
  }
  populateWorkSelector(works); state.workName=work.nome;
  localStorage.setItem(ACCESS_KEY,JSON.stringify({user:authData.user,workName:state.workName,expiresAt:accessExpiresAt}));
  }
  if(!auth){let cachedWorks=[];try{cachedWorks=JSON.parse(localStorage.getItem(WORKS_CACHE_KEY)||'[]');}catch{}populateWorkSelector(cachedWorks);}
  localStorage.setItem('pms_field_work',String(WORK_ID));sessionStorage.setItem('pms_work_id',String(WORK_ID));
  state.db=await openDB();
  await loadSettings();if(auth)await syncNow(false);updateConnectionBadge();
  if('serviceWorker'in navigator){try{await navigator.serviceWorker.register('./sw.js');}catch(e){console.warn('SW',e);}}
  await render();
}
init().catch(err=>{console.error(err);main().innerHTML=`<div class="danger-notice">Não foi possível iniciar o aplicativo. Recarregue a página. Detalhe: ${esc(err.message)}</div>`;});
