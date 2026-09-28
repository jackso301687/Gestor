const form = document.querySelector('#loginForm');
const errorBox = document.querySelector('#loginError');
const username = document.querySelector('#username');
const password = document.querySelector('#password');
const toggle = document.querySelector('#togglePassword');

function afterLogin(user){
  const next=sessionStorage.getItem('pms_return_to');
  sessionStorage.removeItem('pms_return_to');
  window.location.replace(!user?.deve_trocar_senha && /^\/campo\/(?:index\.html)?(?:\?obra_id=\d+)?$/.test(next||'') ? next : '/');
}

async function checkSession() {
  const response = await fetch('/api/auth/me');
  if (response.ok) afterLogin((await response.json()).user);
}

toggle.addEventListener('click', () => {
  const showing = password.type === 'text';
  password.type = showing ? 'password' : 'text';
  toggle.textContent = showing ? 'Mostrar' : 'Ocultar';
  toggle.setAttribute('aria-label', showing ? 'Mostrar senha' : 'Ocultar senha');
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  errorBox.hidden = true;
  [username, password].forEach(field => field.setAttribute('aria-invalid', String(!field.value.trim())));
  if (!username.value.trim() || !password.value) {
    errorBox.textContent = 'Preencha o usuário e a senha para continuar.';
    errorBox.hidden = false;
    return;
  }
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  submit.textContent = 'Verificando…';
  try {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: username.value, password: password.value }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Não foi possível entrar.');
    sessionStorage.setItem('pms_csrf', data.csrf_token);
    for(const key of Object.keys(localStorage))if(key.startsWith('pms_field_access_'))localStorage.removeItem(key);
    afterLogin(data.user);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    password.value = '';
    password.focus();
  } finally {
    submit.disabled = false;
    submit.textContent = 'Entrar';
  }
});

if(location.protocol!=='file:')checkSession().catch(()=>{errorBox.textContent='Servidor indisponível. Inicie o sistema O gestor de Campo e tente novamente.';errorBox.hidden=false;});
