/* app.js — 启动引导：加新上传、tab 绑定、登录态、邀请链接 */
(function (B) {
  const { $ } = B;

  // ---------------- 加新上传 ----------------
  function closeAdd() {
    B.closeDialog('#addModal');
    $('#addFile').value = '';
    $('#addTime').value = '';
    $('#addPlace').value = '';
    $('#addResult').style.display = 'none';
  }

  function showAdd(msg, ok) {
    const el = $('#addResult');
    el.textContent = msg;
    el.className = ok ? 'ok' : 'err';
    el.style.display = 'block';
  }

  async function doAdd() {
    const f = $('#addFile').files[0];
    if (!f) { showAdd('请先选择文件', false); return; }
    const btn = $('#addGo');
    btn.disabled = true;
    btn.textContent = '识别中…';
    const fd = new FormData();
    fd.append('file', f);
    fd.append('taken_at', $('#addTime').value.replace('T', ' '));
    fd.append('place_name', $('#addPlace').value);
    try {
      const r = await B.api('upload', { method: 'POST', body: fd });
      const j = await r.json();
      if (!j.ok) { showAdd('识别失败：' + (j.error || '未知'), false); return; }
      const isNew = j.is_new ? '新鸟种！' : '老朋友，';
      showAdd(`✓ ${isNew}识别为「${j.common_name_cn}」（${j.confidence}%），累计 ${j.total_obs} 次观测`, true);
      btn.textContent = '完成';
      setTimeout(() => { closeAdd(); B.switchTab('wall'); }, 1200);
    } catch (e) {
      showAdd('请求失败：' + e.message, false);
    } finally {
      btn.disabled = false;
      btn.textContent = '上传并识别';
    }
  }

  // ---------------- 邀请链接横幅 ----------------
  function checkInviteLink() {
    const m = location.search.match(/invite=([a-f0-9]+)/);
    if (!m) return;
    $('#regBanner').style.display = 'flex';
    $('#regBtn').onclick = () => B.registerWithInvite(m[1]);
    $('#regSkip').onclick = () => { $('#regBanner').style.display = 'none'; };
    history.replaceState({}, '', location.pathname);
  }

  // ---------------- 启动 ----------------
  async function boot() {
    B.bindWall();
    B.bindRec();
    B.bindRun();
    B.bindAlbums();
    $('#addBtn').onclick = () => B.openDialog('#addModal');
    $('#keyBtn').onclick = () => B.showLogin();
    $('#btnSaveKey').onclick = () => B.saveApiKey();
    $('#btnCloseKey').onclick = () => B.closeDialog('#keyModal');
    $('#btnAddGo').onclick = doAdd;
    $('#btnAddCancel').onclick = closeAdd;
    $('#mask').onclick = () => { B.closeModal(); B.closeDialog('#keyModal'); B.closeDialog('#inviteModal'); B.closeDialog('#addModal'); };

    // tab 点击
    B.$$('.tab[data-t]').forEach((t) => (t.onclick = () => B.switchTab(t.dataset.t)));

    await B.ensureSession();
    B.initBottomNav();
    checkInviteLink();

    let tab = 'wall';
    try { tab = localStorage.getItem('birding_tab') || 'wall'; } catch (e) {}
    B.switchTab(tab);

    // PWA service worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
  }

  B.closeAdd = closeAdd;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window.B);
