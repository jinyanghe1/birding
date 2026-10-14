/* api.js — API 访问层 + 登录态管理 */
(function (B) {
  const { $, esc } = B;

  const API = (p) => '/api/' + p;
  B.API = API;

  function imgUrl(p) {
    if (!p) return '';
    if (p.startsWith('thumbs/')) return '/' + p;      // 相对路径走 Nginx/静态
    return '/img?path=' + encodeURIComponent(p);      // 绝对路径走 FastAPI 代理
  }
  B.imgUrl = imgUrl;

  // ---------------- API Key / 登录态 ----------------
  const LS_KEY = 'birding_api_key';
  const getApiKey = () => { try { return localStorage.getItem(LS_KEY) || ''; } catch (e) { return ''; } };
  const setApiKey = (k) => { try { k ? localStorage.setItem(LS_KEY, k) : localStorage.removeItem(LS_KEY); } catch (e) {} };
  B.getApiKey = getApiKey;
  B.setApiKey = setApiKey;

  function apiHeaders(extra) {
    const k = getApiKey();
    return Object.assign(k ? { 'X-API-Key': k } : {}, extra || {});
  }
  B.apiHeaders = apiHeaders;

  // 统一请求：自动带 key、统一错误提示；401 时触发重新登录提示
  async function api(path, opts = {}) {
    const headers = Object.assign(apiHeaders(), opts.headers || {});
    const r = await fetch(API(path), Object.assign({}, opts, { headers }));
    if (r.status === 401) {
      B.toast('登录已失效，请重新设置 API Key', true);
      B.showLogin();
      throw new Error('401');
    }
    return r;
  }
  B.api = api;
  // JSON 请求：非 2xx 抛错（让调用方的 .catch 兜底生效，而不是把 {detail} 当数据）
  const apiJSON = async (path, opts) => {
    const r = await api(path, opts);
    if (!r.ok) {
      let detail = "";
      try { detail = (await r.json()).detail || ""; } catch (e) { /* 非 JSON */ }
      throw new Error(`${r.status} ${detail}`.trim() || `HTTP ${r.status}`);
    }
    return r.json();
  };
  B.apiJSON = apiJSON;

  // 当前用户（登录态）
  let currentUser = null;
  B.currentUser = () => currentUser;
  const listeners = [];
  B.onAuthChange = (fn) => listeners.push(fn);

  async function ensureSession() {
    const k = getApiKey();
    if (!k) { currentUser = null; listeners.forEach((f) => f(currentUser)); renderUserBadge(); return null; }
    try {
      // POST /auth/session 成功时服务端种 HttpOnly Cookie（<img>/window.open 用）
      const r = await fetch(API('auth/session'), { method: 'POST', headers: apiHeaders() });
      currentUser = r.ok ? ((await r.json()).user || null) : null;
    } catch (e) { currentUser = null; }
    listeners.forEach((f) => f(currentUser));
    renderUserBadge();
    return currentUser;
  }
  B.ensureSession = ensureSession;

  function renderUserBadge() {
    const el = $('#userBadge');
    if (!el) return;
    if (!currentUser) { el.style.display = 'none'; }
    else {
      el.style.display = 'inline-flex';
      el.innerHTML = `<span class="avatar">${esc((currentUser.display_name || 'U')[0])}</span>
        <span>${esc(currentUser.display_name || currentUser.username)}</span>
        <span class="role">${currentUser.role === 'owner' ? '所有者' : '成员'}</span>
        <button onclick="B.logout()">退出</button>`;
    }
    renderScopeChip();
  }

  // 数据范围 chip（路线 C per-user）：匿名=所有者公开数据 / 成员=我的 / owner=全部
  function renderScopeChip() {
    const el = $('#scopeChip');
    if (!el) return;
    el.textContent = !currentUser ? '数据范围：所有者公开数据'
      : currentUser.role === 'owner' ? '数据范围：全部数据'
      : '数据范围：我的数据';
  }
  B.renderScopeChip = renderScopeChip;

  async function logout() {
    try { await fetch(API('auth/logout'), { method: 'POST' }); } catch (e) {}
    setApiKey('');
    currentUser = null;
    listeners.forEach((f) => f(null));
    renderUserBadge();
    B.toast('已退出登录');
    if (B.currentTab() === 'albums') B.switchTab('albums');
  }
  B.logout = logout;

  // 登录弹窗（key 输入）
  function showLogin() {
    B.openDialog('#keyModal');
    $('#apiKeyInput').value = getApiKey();
    $('#apiKeyInput').focus();
  }
  B.showLogin = showLogin;

  async function saveApiKey() {
    const k = $('#apiKeyInput').value.trim();
    if (!k) { B.toast('请输入 API Key', true); return; }
    try {
      const r = await fetch(API('auth/login'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: k }),
      });
      if (!r.ok) { B.toast('API Key 无效', true); return; }
      setApiKey(k);
      B.closeDialog('#keyModal');
      B.toast('登录成功');
      await ensureSession();
      listeners.forEach((f) => f(currentUser));
    } catch (e) { B.toast('登录失败：' + e.message, true); }
  }
  B.saveApiKey = saveApiKey;

  // 邀请注册（?invite=code）
  async function registerWithInvite(code) {
    const name = (prompt(`输入你的名字（例如：妈妈）\n注册后会获得你的专属 API Key`) || '').trim();
    if (!name) return;
    try {
      const r = await fetch(API('invites/' + encodeURIComponent(code) + '/use'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: name }),
      });
      const j = await r.json();
      if (!r.ok || !j.api_key) { B.toast('注册失败：' + (j.detail || '未知错误'), true); return; }
      setApiKey(j.api_key);
      await ensureSession();
      listeners.forEach((f) => f(currentUser));
      B.toast(j.group_name
        ? `注册成功，欢迎 ${j.display_name}！已加入「${j.group_name}」`
        : `注册成功，欢迎 ${j.display_name}！`);
      history.replaceState({}, '', location.pathname);
      $('#regBanner').style.display = 'none';
    } catch (e) { B.toast('注册失败：' + e.message, true); }
  }
  B.registerWithInvite = registerWithInvite;
})(window.B);
