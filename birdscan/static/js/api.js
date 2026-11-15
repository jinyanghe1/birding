/* api.js — API 访问层 + API Key 管理（单用户模型） */
(function (B) {
  const { $ } = B;

  const API = (p) => '/api/' + p;
  B.API = API;

  function imgUrl(p) {
    if (!p) return '';
    if (p.startsWith('thumbs/')) return '/' + p;      // 相对路径走 Nginx/静态
    return '/img?path=' + encodeURIComponent(p);      // 绝对路径走 FastAPI 代理
  }
  B.imgUrl = imgUrl;

  // ---------------- API Key（写操作鉴权，存 localStorage） ----------------
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

  // 统一请求：自动带 key；401（写操作缺 key）时弹 key 设置框
  async function api(path, opts = {}) {
    const headers = Object.assign(apiHeaders(), opts.headers || {});
    const r = await fetch(API(path), Object.assign({}, opts, { headers }));
    if (r.status === 401) {
      B.toast('该操作需要 API Key', true);
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

  // ---------------- API Key 设置弹窗 ----------------
  function showLogin() {
    B.openDialog('#keyModal');
    $('#apiKeyInput').value = getApiKey();
    $('#apiKeyInput').focus();
  }
  B.showLogin = showLogin;

  function saveApiKey() {
    const k = $('#apiKeyInput').value.trim();
    if (!k) { B.toast('请输入 API Key', true); return; }
    setApiKey(k);
    B.closeDialog('#keyModal');
    B.toast('API Key 已保存（写操作将自动携带）');
  }
  B.saveApiKey = saveApiKey;
})(window.B);
