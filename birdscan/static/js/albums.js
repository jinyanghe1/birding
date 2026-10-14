/* albums.js — 家庭共享相册（多用户 / 邀请 / 上传 / 下载 / 删除） */
(function (B) {
  const { $, esc, api, apiJSON } = B;
  let CURRENT_ALBUM = null;
  let ALBUMS = [];

  const isOwner = () => (B.currentUser() || {}).role === 'owner';
  const myId = () => (B.currentUser() || {}).id;

  function loginPrompt() {
    $('#albumList').innerHTML = `<div class="empty">家庭相册需要登录。<br><br>
      <button class="btn primary" onclick="B.showLogin()">设置 API Key 登录</button></div>`;
    $('#albumDetail').style.display = 'none';
  }

  async function loadAlbums() {
    if (!B.getApiKey()) { loginPrompt(); return; }
    const user = await B.ensureSession();
    if (!user) { loginPrompt(); return; }

    ALBUMS = await apiJSON('albums').catch(() => []);
    CURRENT_ALBUM = null;
    $('#albumDetail').style.display = 'none';

    // 相册二级 tab
    $('#albumTabs').innerHTML = ['<div class="tab on" data-album="all">全部</div>']
      .concat(ALBUMS.map((a) => `<div class="tab" data-album="${a.id}">${esc(a.name)}</div>`))
      .join('');
    B.$$('#albumTabs .tab').forEach((t) => {
      t.onclick = () => {
        B.$$('#albumTabs .tab').forEach((x) => x.classList.remove('on'));
        t.classList.add('on');
        const aid = t.dataset.album;
        if (aid === 'all') { CURRENT_ALBUM = null; $('#albumDetail').style.display = 'none'; loadAllPhotos(); }
        else openAlbum(parseInt(aid, 10), ALBUMS.find((a) => a.id == aid));
      };
    });

    // owner 专属：邀请家人
    $('#btnInvite').style.display = isOwner() ? '' : 'none';

    if (!ALBUMS.length) {
      $('#albumList').innerHTML = '<div class="empty">还没有相册。创建一个，或让家人发邀请链接给你。</div>';
      return;
    }
    loadAllPhotos();
  }

  async function loadAllPhotos() {
    const all = [];
    for (const a of ALBUMS) {
      const photos = await apiJSON(`albums/${a.id}/photos?limit=200`).catch(() => []);
      all.push(...photos.map((p) => ({ ...p, album_name: a.name, album_id: a.id })));
    }
    renderPhotos(all);
  }

  function renderPhotos(photos) {
    const box = $('#albumList');
    if (!photos.length) {
      box.innerHTML = '<div class="empty">还没有照片。选一个相册，点「上传照片」添加。</div>';
      return;
    }
    box.innerHTML = `<div class="albumGrid">${photos.map((p) => `
      <div class="albumTile" data-id="${p.id}">
        <img src="${B.API('photos/' + p.id + '/thumb')}" loading="lazy" alt="${esc(p.orig_name || '')}"
             title="${esc(p.uploader_name || '')} · ${esc((p.created_at || '').slice(0, 10))}${p.sync_status === 'synced' ? ' · 已同步' : ''}"
             onclick="window.open('${B.API('photos/' + p.id)}')">
        <div class="cap">${esc(p.orig_name || p.filename || '')}</div>
        ${(isOwner() || p.uploader_id === myId())
          ? `<button class="del" title="删除照片" onclick="B.deletePhoto(${p.id}, this)">×</button>` : ''}
      </div>`).join('')}</div>`;
  }

  async function openAlbum(id, album) {
    CURRENT_ALBUM = id;
    $('#albumDetail').style.display = 'block';
    $('#albumTitle').textContent = album ? album.name : '';
    const photos = await apiJSON(`albums/${id}/photos?limit=200`).catch(() => []);
    renderPhotos(photos.map((p) => ({ ...p, album_name: album.name, album_id: id })));
    $('#albumList').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ---------------- 创建相册 ----------------
  async function createAlbum() {
    const name = $('#newAlbumName').value.trim();
    if (!name) { B.toast('请输入相册名', true); return; }
    try {
      await api('albums', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }) });
      $('#newAlbumName').value = '';
      B.toast('相册已创建');
      loadAlbums();
    } catch (e) { B.toast('创建失败：' + e.message, true); }
  }

  // ---------------- 邀请家人 ----------------
  function showInviteModal() {
    if (!isOwner()) { B.toast('只有所有者能生成邀请码', true); return; }
    $('#inviteAlbums').innerHTML =
      '<div class="tiny" style="color:var(--tx3);margin-bottom:8px">家人注册后只能看到你勾选的相册</div>' +
      ALBUMS.map((a) => `<label>
        <input type="checkbox" value="${a.id}" checked>
        <span>${esc(a.name)}</span>
        ${a.is_default ? '<span class="tiny" style="color:var(--tx3)">（默认相册）</span>' : ''}
      </label>`).join('');
    $('#inviteResult').style.display = 'none';
    B.openDialog('#inviteModal');
  }

  async function generateInvite() {
    const checked = B.$$('#inviteAlbums input:checked').map((i) => parseInt(i.value, 10));
    if (!checked.length) { B.toast('请至少勾选一个相册', true); return; }
    try {
      const r = await api('invites', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ album_ids: checked, max_uses: 1 }) });
      const j = await r.json();
      if (!r.ok) { B.toast('生成失败：' + (j.detail || '未知错误'), true); return; }
      $('#inviteLink').textContent = `${location.origin}/?invite=${j.code}`;
      $('#inviteResult').style.display = 'block';
    } catch (e) { B.toast('生成失败：' + e.message, true); }
  }

  function copyInviteLink() {
    const link = $('#inviteLink').textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(link) : Promise.reject())
      .then(() => B.toast('链接已复制'))
      .catch(() => { $('#inviteLink').select && document.execCommand('copy'); B.toast('链接已复制'); });
  }

  // ---------------- 上传 ----------------
  function bindDropzone() {
    const dz = $('#albumDrop');
    if (!dz) return;
    dz.onclick = () => $('#albumFiles').click();
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('hover'); };
    dz.ondragleave = () => dz.classList.remove('hover');
    dz.ondrop = (e) => {
      e.preventDefault(); dz.classList.remove('hover');
      if (e.dataTransfer.files.length) { $('#albumFiles').files = e.dataTransfer.files; uploadToAlbum(); }
    };
  }

  async function uploadToAlbum() {
    if (!CURRENT_ALBUM) { B.toast('先选一个相册', true); return; }
    const files = $('#albumFiles').files;
    if (!files.length) { B.toast('请选择照片', true); return; }
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    const prog = $('#uploadProgress');
    prog.innerHTML = `<div class="tiny">上传中 ${files.length} 张…</div>`;
    try {
      const r = await api(`albums/${CURRENT_ALBUM}/photos`, { method: 'POST', body: fd });
      const res = await r.json();
      const ok = res.filter((x) => x.ok).length;
      const dup = res.filter((x) => x.duplicate).length;
      const bad = res.filter((x) => !x.ok);
      prog.innerHTML = `<div class="tiny" style="color:var(--teal)">上传完成：${ok} 张成功${dup ? `，${dup} 张重复已跳过` : ''}</div>` +
        (bad.length ? `<div class="tiny" style="color:var(--red);margin-top:4px">${bad.length} 张失败：${bad.map((x) => esc(x.name) + '（' + esc(x.error || '') + '）').join('；')}</div>` : '');
      $('#albumFiles').value = '';
      setTimeout(() => openAlbum(CURRENT_ALBUM, ALBUMS.find((a) => a.id === CURRENT_ALBUM)), 900);
    } catch (e) {
      prog.innerHTML = `<div class="tiny" style="color:var(--red)">上传失败：${esc(e.message)}</div>`;
    }
  }

  // ---------------- 下载 / 删除 ----------------
  function downloadAlbum() {
    if (!CURRENT_ALBUM) { B.toast('先选一个相册', true); return; }
    window.open(B.API('albums/' + CURRENT_ALBUM + '/download'), '_blank');
  }

  async function deletePhoto(pid, btn) {
    if (!confirm('确认删除这张照片？')) return;
    btn.disabled = true;
    try {
      await api('photos/' + pid, { method: 'DELETE' });
      btn.closest('.albumTile').remove();
      B.toast('照片已删除');
    } catch (e) { B.toast('删除失败：' + e.message, true); btn.disabled = false; }
  }

  async function deleteAlbum() {
    if (!CURRENT_ALBUM) { B.toast('先选一个相册', true); return; }
    const name = $('#albumTitle').textContent;
    if (!confirm(`确认删除相册「${name}」？\n\n相册里的照片会被删除，此操作不可恢复。`)) return;
    const input = prompt(`请输入相册名「${name}」确认删除：`);
    if (input !== name) { B.toast('相册名不匹配，取消删除', true); return; }
    try {
      const r = await api('albums/' + CURRENT_ALBUM, { method: 'DELETE' });
      const j = await r.json();
      if (!r.ok) { B.toast('删除失败：' + (j.detail || '未知错误'), true); return; }
      B.toast(`相册已删除（清理 ${j.gc_photos || 0} 张照片）`);
      CURRENT_ALBUM = null;
      loadAlbums();
    } catch (e) { B.toast('删除失败：' + e.message, true); }
  }

  function bind() {
    $('#btnCreateAlbum').onclick = createAlbum;
    $('#btnInvite').onclick = showInviteModal;
    $('#btnGenerateInvite').onclick = generateInvite;
    $('#btnCopyInvite').onclick = copyInviteLink;
    $('#btnAlbumUpload').onclick = uploadToAlbum;
    $('#btnAlbumDownload').onclick = downloadAlbum;
    $('#btnAlbumDelete').onclick = deleteAlbum;
    bindDropzone();
  }

  B.registerTab('albums', { show: loadAlbums });
  B.createAlbum = createAlbum;
  B.deletePhoto = deletePhoto;
  B.bindAlbums = bind;
})(window.B);
