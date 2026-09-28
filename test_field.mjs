// Regressões do fluxo offline/sincronização sem acessar o banco operacional.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('./campo/app.js', import.meta.url), 'utf8').replace(/init\(\)\.catch\(err=>\{[\s\S]*?\}\);\s*$/, '');
function setup(fetchImpl, online = true) {
  const storage = () => {
    const data = new Map();
    return {getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k)};
  };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {textContent:'', className:'', classList:{add(){}, remove(){}}, addEventListener(){}, replaceChildren(){}, appendChild(){}});
    return elements.get(id);
  };
  const ctx = {
    fetch: fetchImpl, navigator: {onLine: online}, location: {protocol:'http:', search:'?obra_id=1', pathname:'/campo/index.html', replace(){}},
    localStorage: storage(), sessionStorage: storage(), document: {querySelector: element, querySelectorAll:()=>[],addEventListener(){}},
    window: {addEventListener(){}}, AbortSignal, URLSearchParams, Date, Intl, JSON, Math, Number, String, Set, Map,
    clearTimeout, setTimeout, console,
  };
  runInNewContext(source, ctx);
  const applyRemoteSnapshot = ctx.applyRemoteSnapshot;
  // Não é um banco falso de produto: só isola a decisão de rede da persistência.
  runInNewContext('dbGetAll = async () => []; applyRemoteSnapshot = async () => {}; loadSettings = async () => {}; render = async () => {}', ctx);
  return {ctx, badge: element('#connectionBadge'), state: runInNewContext('state', ctx), applyRemoteSnapshot};
}
const response = (status, data) => ({ok:status >= 200 && status < 300,status,json:async()=>data});

test('não mostra sincronizado quando a gravação falha', async () => {
  const {ctx,state,badge} = setup(async path => path === '/api/auth/me'
    ? response(200,{csrf_token:'teste',user:{},expires_at:'2030-01-01T00:00:00Z'})
    : path.includes('snapshot') ? response(200,{}) : response(500,{error:'Falha de gravação'}));
  assert.equal(await runInNewContext('syncNow(true)',ctx),false);
  assert.equal(state.syncState,'local');
  assert.equal(badge.textContent,'Salvo no aparelho');
});

test('não envia dados quando a leitura do servidor falha', async () => {
  let pushes=0;
  const {ctx,state} = setup(async (path,options) => {
    if(options?.method==='POST')pushes++;
    return path === '/api/auth/me' ? response(200,{csrf_token:'teste',user:{}}) : response(503,{error:'Indisponível'});
  });
  assert.equal(await runInNewContext('syncNow(true)',ctx),false);
  assert.equal(state.syncState,'local');
  assert.equal(pushes,0);
});

test('sincronização bem sucedida confirma resposta do servidor', async () => {
  const {ctx,state,badge} = setup(async path => path === '/api/auth/me'
    ? response(200,{csrf_token:'teste',user:{},expires_at:'2030-01-01T00:00:00Z'})
    : path.includes('snapshot') ? response(200,{}) : response(200,{ok:true,snapshot:{}}));
  assert.equal(await runInNewContext('syncNow(true)',ctx),true);
  assert.equal(state.syncState,'synced');
  assert.equal(badge.textContent,'Sincronizado');
});

test('resposta canônica do servidor substitui registro local com updatedAt igual', async () => {
  const {ctx,state,applyRemoteSnapshot} = setup(async()=>response(200,{}));
  const stamp = '2026-09-25T12:00:00.000Z';
  const records = new Map([['worker',{id:'worker',name:'Ana',updatedAt:stamp}]]);
  state.db = {transaction: () => {
    const transaction = {
      objectStore: () => ({
        get: id => {
          const request = {};
          queueMicrotask(() => { request.result=records.get(id); request.onsuccess?.(); });
          return request;
        },
        put: value => records.set(value.id,value),
      }),
    };
    setTimeout(() => transaction.oncomplete?.(),0);
    return transaction;
  }};
  await applyRemoteSnapshot({employees:[{id:'worker',name:'ANA',updatedAt:stamp}]});
  assert.equal(records.get('worker').name,'ANA');
});

test('backup inválido é rejeitado antes de importar', () => {
  const {ctx} = setup(async()=>{});
  assert.throws(()=>runInNewContext('validateSnapshot({employees:[{id:"<img>",updatedAt:"2026-09-25T12:00:00Z"}]})',ctx));
  assert.throws(()=>runInNewContext('validateSnapshot({appointments:[{id:"point",updatedAt:"2026-09-25T12:00:00Z",date:"2026-09-25"}]})',ctx));
  assert.throws(()=>runInNewContext('validateSnapshot({employees:[{id:"x",name:"Pessoa",updatedAt:"amanhã"}]})',ctx));
});

test('backup não aceita apontamento sem cadastro relacionado', async () => {
  const {ctx} = setup(async()=>{});
  ctx.backup = {
    employees:[{id:'worker',name:'Pessoa',updatedAt:'2026-09-25T12:00:00Z'}],
    services:[{id:'service',name:'Serviço',updatedAt:'2026-09-25T12:00:00Z'}],
    appointments:[{id:'point',date:'2026-09-25',employeeIds:['worker'],locationIds:['local-inexistente'],serviceId:'service',status:'em_andamento',updatedAt:'2026-09-25T12:00:00Z'}],
  };
  await assert.rejects(runInNewContext('validateBackupRelations(backup)',ctx),/locations/);
});

test('cópia local só abre offline dentro da sessão autorizada', async () => {
  const {ctx,state} = setup(async()=>{throw new Error('sem rede');},false);
  const future = Date.now() + 60_000;
  ctx.localStorage.setItem('pms_field_access_1',JSON.stringify({user:{},workName:'ATLANTA',expiresAt:future}));
  runInNewContext('openDB = async () => ({})',ctx);
  await runInNewContext('init()',ctx);
  assert.equal(state.workName,'ATLANTA');
  assert.equal(state.syncState,'offline');
  ctx.localStorage.setItem('pms_field_access_1',JSON.stringify({user:{},workName:'ATLANTA',expiresAt:Date.now()-1}));
  await assert.rejects(runInNewContext('init()',ctx),/Conecte-se/);
});
