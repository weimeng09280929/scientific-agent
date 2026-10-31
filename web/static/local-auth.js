/* Local account gate. Application scripts start only after identity is resolved. */
(async () => {
  const nativeFetch = window.fetch.bind(window);
  const app = document.querySelector('.app');
  const gate = document.getElementById('account-gate');
  const message = document.getElementById('account-message');
  let current = null;
  let csrf = '';
  let booted = false;
  let generation = 0;
  let registering = false;
  let registrationEnabled = false;
  let locked = false;
  const requests = new Set();
  const rawSet = Storage.prototype.setItem;
  const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('sra-account') : null;
  const scripts = Array.from(document.querySelectorAll('script[data-account-script]'), tag => tag.src);
  function showLogin(text) {
    current = null; csrf = ''; generation++;
    app.hidden = true; gate.hidden = false;
    message.textContent = text || '';
  }
  function notify() {
    if (channel) channel.postMessage({type:'changed'});
    else rawSet.call(localStorage, 'sra.account-event', String(Date.now()) + Math.random());
  }
  function expire() {
    if (locked) return;
    locked = true;
    showLogin('登录状态已变化，请重新登录。');
    for (const controller of requests) controller.abort();
    location.replace('/?login=expired');
  }
  async function boot() {
    if (booted) return;
    booted = true;
    for (const src of scripts) {
      await new Promise((resolve, reject) => {
        const tag = document.createElement('script');
        tag.src = src; tag.onload = resolve; tag.onerror = reject;
        document.body.appendChild(tag);
      });
    }
  }
  function scopedStorage() {
    const proto = Storage.prototype;
    const get = proto.getItem, set = proto.setItem, remove = proto.removeItem;
    const scoped = key => current ? (current.id === 'local' ? String(key) : `sra.account.${current.id}:${String(key)}`) : null;
    proto.getItem = function(key) { const k = scoped(key); return k ? get.call(this, k) : null; };
    proto.setItem = function(key, value) { const k = scoped(key); if (k) set.call(this, k, value); };
    proto.removeItem = function(key) { const k = scoped(key); if (k) remove.call(this, k); };
  }
  scopedStorage();
  window.fetch = async (input, init={}) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const options = {...init};
    const protectedAPI = url.origin === location.origin && url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/auth/');
    if (protectedAPI && (!current || locked)) throw new DOMException('请重新登录', 'AbortError');
    const controller = new AbortController();
    requests.add(controller);
    const signal = init.signal || (input instanceof Request ? input.signal : undefined);
    options.signal = signal ? AbortSignal.any([signal,controller.signal]) : controller.signal;
    if (url.origin === location.origin && url.pathname.startsWith('/api/')) {
      options.headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      if (csrf) options.headers.set('X-SRA-CSRF', csrf);
    }
    const captured = generation;
    try {
      const response = await nativeFetch(input, options);
      if (protectedAPI && captured !== generation) throw new DOMException('账号已切换', 'AbortError');
      if (protectedAPI && response.status === 401 && current) {
        expire();
        throw new DOMException('请重新登录', 'AbortError');
      }
      return response;
    } finally { requests.delete(controller); }
  };
  async function session() {
    try {
      const response = await nativeFetch('/api/auth/me', {cache:'no-store'});
      if (!response.ok) { showLogin(location.search.includes('login=expired') ? '登录已失效，请重新登录。原账号草稿保留在原账号下。' : '请登录以查看自己的研究资料。'); return; }
      const result = await response.json();
      if (result.mode === 'local') {
        current = {id:'local'}; gate.hidden = true; app.hidden = false;
        document.title = '科研 Agent · 本地演示';
        document.getElementById('account-controls').hidden = false;
        document.getElementById('account-email').textContent = '历史项目演示 · 无需登录';
        document.getElementById('account-logout').hidden = true;
        await boot(); return;
      }
      if (current && current.id !== result.user.id) location.reload();
      current = result.user; csrf = result.csrf_token; generation++;
      gate.hidden = true; app.hidden = false;
      const account = document.getElementById('account-controls');
      account.hidden = false;
      document.getElementById('account-email').textContent = result.user.email + ' · 邮箱未验证';
      await boot();
    } catch (_) { showLogin('无法核对登录状态，请稍后刷新。'); }
  }
  function passwordVisibility(button, visible) {
    document.getElementById(button.dataset.passwordToggle).type = visible ? 'text' : 'password';
    const label = (visible ? '隐藏' : '显示') + button.dataset.passwordName;
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', label);
    button.title = label;
  }
  function hidePasswords() {
    document.querySelectorAll('[data-password-toggle]').forEach(button=>passwordVisibility(button,false));
  }
  document.querySelectorAll('[data-password-toggle]').forEach(button=>{
    button.addEventListener('click',()=>passwordVisibility(button,button.getAttribute('aria-pressed') !== 'true'));
  });
  function setFormMode(value) {
    registering = value && registrationEnabled;
    const form = document.getElementById('account-form');
    form.password.value = ''; form.confirm_password.value = '';
    hidePasswords();
    form.password.minLength = registering ? 8 : 1;
    form.password.autocomplete = registering ? 'new-password' : 'current-password';
    form.confirm_password.disabled = !registering;
    form.confirm_password.required = registering;
    document.getElementById('account-confirm-row').hidden = !registering;
    document.getElementById('account-title').textContent = registering ? '注册本地账号' : '科研 Agent';
    document.getElementById('account-subtitle').textContent = registering ? '设置自己的邮箱标识与密码，邮箱暂不验证' : '登录后继续你的研究工作台';
    document.getElementById('account-submit').textContent = registering ? '创建账号' : '登录';
    document.getElementById('account-switch').textContent = registering ? '已有账号？返回登录' : '注册新账号';
    message.textContent = '';
  }
  document.getElementById('account-switch').addEventListener('click',()=>setFormMode(!registering));
  document.getElementById('account-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (registering && form.password.value !== form.confirm_password.value) {
      message.textContent = '两次密码不一致，请重新核对。'; return;
    }
    const creating = registering;
    const submit = document.getElementById('account-submit'), toggle = document.getElementById('account-switch');
    submit.disabled = toggle.disabled = true;
    message.textContent = creating ? '正在创建账号…' : '正在登录…';
    const payload = {email:form.email.value,password:form.password.value};
    if (creating) payload.confirm_password = form.confirm_password.value;
    const response = await nativeFetch(creating ? '/api/auth/register' : '/api/auth/login', {
      method:'POST', headers:{'Content-Type':'application/json','X-SRA-Login':'1'},
      body:JSON.stringify(payload)
    }).catch(() => null);
    form.password.value = ''; form.confirm_password.value = '';
    hidePasswords();
    submit.disabled = toggle.disabled = false;
    if (!response?.ok) {
      const data = await response?.json().catch(()=>({}));
      message.textContent = creating ? (typeof data?.detail === 'string' ? data.detail : '注册结果未确认，请先尝试登录；未成功时再重试。') : '登录失败，请检查邮箱和密码，或稍后重试。'; return;
    }
    if (creating) {
      setFormMode(false);
      message.textContent = '注册成功，邮箱尚未验证。请使用你刚设置的密码登录。';
      form.password.focus(); return;
    }
    notify();
    location.replace('/');
  });
  document.getElementById('account-logout').addEventListener('click', async () => {
    const result = current && csrf ? await nativeFetch('/api/auth/logout', {method:'POST',headers:{'X-SRA-CSRF':csrf}}).catch(()=>null) : null;
    if (!result || (!result.ok && result.status !== 401)) {
      alert('退出未成功，请保持页面打开并重试。'); return;
    }
    showLogin('已退出。');
    notify();
    location.replace('/');
  });
  if (channel) channel.onmessage = expire;
  else window.addEventListener('storage', event => { if(event.key === 'sra.account-event') expire(); });
  window.addEventListener('pageshow', e => { if (e.persisted) location.reload(); });
  async function checkSession() {
    if (!current || current.id === 'local') return;
    const response = await nativeFetch('/api/auth/me', {cache:'no-store'}).catch(()=>null);
    if (!response) return;
    const data = response.ok ? await response.json() : null;
    if (!data || data.user.id !== current.id || data.csrf_token !== csrf) expire();
  }
  setInterval(checkSession, 30000);
  window.addEventListener('focus', checkSession);
  try {
    const options = await nativeFetch('/api/auth/options', {cache:'no-store'});
    if (options.ok) registrationEnabled = (await options.json()).registration_enabled === true;
  } catch (_) { /* An unavailable capability never exposes an unusable entry. */ }
  document.getElementById('account-switch').hidden = !registrationEnabled;
  await session();
})();
