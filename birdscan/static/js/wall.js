/* wall.js — 统计卡片 + 物种墙 + 物种详情 */
(function (B) {
  const { $, esc, apiJSON, openModal, imgUrl } = B;
  let ALL = [];
  let FULLS = [];
  let fullIndex = 0;

  async function load() {
    const g = $('#grid');
    g.innerHTML = '<div class="empty">正在加载物种墙…</div>';
    let st, sp;
    try {
      [st, sp] = await Promise.all([
        apiJSON('stats').catch(() => ({})),
        apiJSON('species?min_conf=' + mc()),
      ]);
    } catch (e) {
      g.innerHTML = `<div class="empty">物种墙加载失败：${esc(e.message || String(e))}<br>
        <button class="btn" onclick="B.loadWall()">重新加载</button></div>`;
      return;
    }
    const list = sp;
    const prot1 = list.filter((s) => s.china_protection === '一级').length;
    const prot2 = list.filter((s) => s.china_protection === '二级').length;
    const iucn = list.filter((s) => ['CR', 'EN', 'VU'].includes(s.iucn_status)).length;
    $('#stats').innerHTML = [
      ['鸟种', st.species_confident ?? st.species ?? 0],
      ['观测次数', st.observations_confident ?? st.observations ?? 0],
      ['地点', st.places_confident ?? st.places ?? 0],
      ['照片', st.photos ?? 0],
      ['待复核', st.needs_review ?? 0],
      ['国家一级', prot1 || '—'],
      ['国家二级', prot2 || '—'],
      ['IUCN 受胁', iucn || '—'],
    ].map(([l, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');

    ALL = list;
    if (!list.length) {
      g.innerHTML = '<div class="empty">还没有观测记录。<br>去「扫描与识别」标签页跑一次扫描。</div>';
      return;
    }
    g.innerHTML = list.map((s) => {
      const src = s.thumb || s.img;
      return `<div class="card" data-id="${s.id}">
        ${src ? `<img class="ph" loading="lazy" src="${imgUrl(src)}" onerror="this.className='ph empty';this.textContent='无图'">`
              : '<div class="ph empty">无图</div>'}
        <div class="bd">
          <div class="nm" title="${esc(s.common_name_cn)}">${esc(s.common_name_cn)}</div>
          <div class="sc">${esc(s.scientific_name || '')}</div>
          <div class="ft"><span class="badge">${s.cnt} 次</span>
            <span>${esc((s.last_date || '').slice(5))}</span></div>
        </div></div>`;
    }).join('');
    g.querySelectorAll('.card').forEach((c) => (c.onclick = () => openSpecies(c.dataset.id)));
  }

  async function openSpecies(id) {
    const d = await apiJSON('species/' + id);
    const s = d.species;
    const photos = d.observations.flatMap((o) => (o.photos || []).map((p) => ({ p, o })));
    openModal(`
      <h1>${esc(s.common_name_cn)}</h1>
      <div class="tiny" style="font-style:italic">${esc(s.scientific_name || '')}</div>
      <div class="tiny" style="margin-top:6px">
        ${[s.order_cn, s.family_cn].filter(Boolean).map(esc).join(' · ')}
        ${s.iucn_status ? ' · IUCN ' + esc(s.iucn_status) : ''}
        ${s.china_protection ? `<span class="prot">国家${esc(s.china_protection)}保护</span>` : ''}
      </div>
      ${s.summary ? `<div class="meta">${esc(s.summary)}</div>` : ''}
      ${extLinksHtml(s.links_json)}
      ${s.scientific_name ? `<div style="margin:10px 0">
        <a class="btn sm" href="https://xeno-canto.org/explore?query=sp%3A%22${encodeURIComponent(s.scientific_name)}%22"
           target="_blank" rel="noopener">🔊 听叫声（Xeno-canto）</a>
        <span class="tiny" style="margin-left:8px">跳转到 Xeno-canto 搜索页</span>
      </div>` : ''}
      <div style="margin:12px 0" class="tiny">共 <b style="color:var(--amber)">${d.count}</b> 次观测
        ${d.places.length ? ' · 地点：' + d.places.slice(0, 6).map(esc).join('、') : ''}</div>
      ${photos.length ? `<div class="gal">${photos.slice(0, 24).map((x, i) =>
        `<div class="galItem">
          <img loading="lazy" src="${imgUrl(x.p.thumb_cache || x.p.image_path)}"
            title="${esc(x.o.obs_date)} ${esc(x.o.place_name || '')}"
            data-full-index="${i}" tabindex="0" role="button" aria-label="预览全图">
          <button class="btn sm fullBtn" data-full-index="${i}">预览全图</button>
        </div>`).join('')}</div>` : ''}
      <h2 style="margin-top:18px">观测记录</h2>
      ${d.observations.map((o) => `<div class="obs" data-obs="${o.id}" data-name="${esc(s.common_name_cn)}">
        <span class="d">${esc(o.obs_date)}</span>
        <span class="p">${esc(o.place_name || '（无地点）')}</span>
        ${o.confidence != null ? `<span class="tiny"> 置信度 ${(o.confidence * 100).toFixed(0)}%</span>` : ''}
        ${o.notes ? `<div class="n">${esc(o.notes)}</div>` : ''}
        <div class="acts" style="margin-top:6px">
          <button class="btn sm danger" onclick="B.deleteObs(${o.id}, this, B.afterObsChange)">删除</button>
          <button class="btn sm" onclick="B.reassignObs(${o.id}, this, B.afterObsChange)">修正</button>
        </div>
      </div>`).join('')}`);
    FULLS = photos.slice(0, 24);
    document.querySelectorAll('[data-full-index]').forEach((el) => {
      el.onclick = () => openFull(Number(el.dataset.fullIndex));
      if (el.tagName === 'IMG') el.onkeydown = (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFull(Number(el.dataset.fullIndex)); }
      };
    });
  }

  function fullKindLabel(p) {
    if (p.full_kind === 'original') return '原始分辨率';
    if (p.full_kind === 'frame') return '视频抽帧';
    if (p.full_kind === 'derivative') return '本地最清晰衍生图 · 原图仅在 iCloud';
    return '全图尚未同步，当前显示缩略图';
  }

  function openFull(index) {
    if (!FULLS.length) return;
    fullIndex = (index + FULLS.length) % FULLS.length;
    const { p, o } = FULLS[fullIndex];
    const thumb = imgUrl(p.thumb_cache || p.image_path);
    const src = p.full_url || thumb;
    let box = $('#fullViewer');
    if (!box) {
      document.body.insertAdjacentHTML('beforeend', `<div id="fullViewer" class="fullViewer" role="dialog" aria-modal="true" aria-label="全图预览">
        <div class="fullShade" data-full-close></div>
        <div class="fullTop"><span id="fullMeta"></span><button class="fullClose" data-full-close aria-label="关闭全图">×</button></div>
        <button class="fullNav prev" id="fullPrev" aria-label="上一张">‹</button>
        <div class="fullStage"><img id="fullImage" alt="鸟类全图"><div class="fullLoading" id="fullLoading">正在加载全图…</div></div>
        <button class="fullNav next" id="fullNext" aria-label="下一张">›</button>
        <div class="fullFoot"><span id="fullKind"></span><a id="fullNewTab" target="_blank" rel="noopener">新窗口打开 ↗</a></div>
      </div>`);
      box = $('#fullViewer');
      box.querySelectorAll('[data-full-close]').forEach((el) => { el.onclick = closeFull; });
      $('#fullPrev').onclick = () => renderFull(fullIndex - 1);
      $('#fullNext').onclick = () => renderFull(fullIndex + 1);
    }
    box.style.display = 'grid';
    document.body.classList.add('fullOpen');
    renderFull(fullIndex, thumb);
    $('.fullClose').focus();
  }

  function renderFull(index, knownThumb) {
    fullIndex = (index + FULLS.length) % FULLS.length;
    const { p, o } = FULLS[fullIndex];
    const im = $('#fullImage');
    const loading = $('#fullLoading');
    const thumb = knownThumb || imgUrl(p.thumb_cache || p.image_path);
    loading.style.display = 'block';
    $('#fullMeta').textContent = `${o.obs_date || ''} · ${o.place_name || '无地点'} · ${fullIndex + 1}/${FULLS.length}`;
    $('#fullKind').textContent = fullKindLabel(p) +
      (p.full_width ? ` · ${p.full_width}×${p.full_height}` : '');
    $('#fullNewTab').href = p.full_url || thumb;
    im.onerror = () => {
      im.onerror = null;
      im.src = thumb;
      loading.style.display = 'none';
      $('#fullKind').textContent = '全图加载失败，已回退到缩略图';
    };
    im.onload = () => { loading.style.display = 'none'; };
    im.src = p.full_url || thumb;
  }

  function closeFull() {
    const box = $('#fullViewer');
    if (box) box.style.display = 'none';
    document.body.classList.remove('fullOpen');
  }

  function extLinksHtml(lj) {
    if (!lj) return '';
    let links;
    try { links = JSON.parse(lj); } catch (e) { return ''; }
    const ks = Object.keys(links);
    if (!ks.length) return '';
    return `<div class="ext">${ks.map((k) =>
      `<a href="${links[k]}" target="_blank" rel="noopener">${esc(k)} ↗</a>`).join('')}</div>`;
  }

  /** 删除/修正后：关闭详情弹窗并刷新物种墙（共享逻辑在 review.js）。 */
  function afterObsChange() {
    setTimeout(() => { B.closeModal(); load(); }, 500);
  }

  const mc = () => $('#mc').value;

  // 事件
  let tm;
  function bind() {
    $('#q').oninput = () => { clearTimeout(tm); tm = setTimeout(load, 250); };
    $('#ord').onchange = load;
    $('#mc').onchange = () => {
      load();
      if ($('#t-viz').style.display !== 'none' && B.reloadViz) B.reloadViz();
      if (B.reloadMap) B.reloadMap();
    };
  }

  B.registerTab('wall', { show: load });
  B.loadWall = load;
  B.openSpecies = openSpecies;
  B.openFull = openFull;
  document.addEventListener('keydown', (e) => {
    const box = $('#fullViewer');
    if (!box || box.style.display === 'none') return;
    if (e.key === 'Escape') closeFull();
    else if (e.key === 'ArrowLeft') renderFull(fullIndex - 1);
    else if (e.key === 'ArrowRight') renderFull(fullIndex + 1);
    else return;
    e.preventDefault();
    e.stopImmediatePropagation();
  }, true);
  B.afterObsChange = afterObsChange;
  B.bindWall = bind;
})(window.B);
