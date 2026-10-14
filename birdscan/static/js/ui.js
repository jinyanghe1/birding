/* ui.js — 基础 UI 助手（全局命名空间 B） */
window.B = window.B || {};

(function (B) {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let toastTimer = null;
  function toast(msg, isErr = false) {
    let el = $('#toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'show' + (isErr ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = ''; }, 2600);
  }

  function openModal(html) {
    $('#modal').innerHTML = html +
      '<span class="x" onclick="B.closeModal()">&times;</span>';
    $('#modal').style.display = 'block';
    $('#mask').style.display = 'block';
  }
  function closeModal() {
    $('#modal').style.display = 'none';
    $('#mask').style.display = 'none';
  }
  function openDialog(id) { $(id).style.display = 'block'; $('#mask').style.display = 'block'; }
  function closeDialog(id) { $(id).style.display = 'none'; $('#mask').style.display = 'none'; }

  // Tab 注册表：name -> { show(), hide?() }
  const tabs = {};
  B.registerTab = (name, hooks) => { tabs[name] = hooks; };

  let currentTab = 'wall';
  function switchTab(name) {
    currentTab = name;
    $$('.tab[data-t]').forEach((t) => t.classList.toggle('on', t.dataset.t === name));
    $$('.bottomNav a').forEach((a) => a.classList.toggle('on', a.dataset.tab === name));
    ['wall', 'map', 'viz', 'rec', 'albums', 'review', 'run'].forEach((k) => {
      const el = $('#t-' + k);
      if (el) el.style.display = k === name ? 'block' : 'none';
    });
    if (tabs[name] && tabs[name].show) tabs[name].show();
    try { localStorage.setItem('birding_tab', name); } catch (e) { /* 私密模式 */ }
  }
  B.switchTab = switchTab;
  B.currentTab = () => currentTab;

  // 底部导航（手机端）
  function initBottomNav() {
    if (window.innerWidth > 768) return;
    $('#bottomNav').classList.add('show');
    $$('.bottomNav a').forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); switchTab(a.dataset.tab); };
    });
  }

  B.$ = $; B.$$ = $$; B.esc = esc;
  B.toast = toast; B.openModal = openModal; B.closeModal = closeModal;
  B.openDialog = openDialog; B.closeDialog = closeDialog;
  B.initBottomNav = initBottomNav;
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
})(window.B);
