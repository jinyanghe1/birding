/* wall.js — 统计卡片 + 物种墙 + 物种详情 */
(function (B) {
  const { $, esc, apiJSON, openModal, imgUrl } = B;
  let ALL = [];

  async function load() {
    const [st, sp] = await Promise.all([
      apiJSON('stats').catch(() => ({})),
      apiJSON('species?min_conf=' + mc()).catch(() => []),
    ]);
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
    const g = $('#grid');
    if (!list.length) {
      const member = (B.currentUser() || {}).role === 'member';
      g.innerHTML = member
        ? '<div class="empty">你还没有观测记录。<br>点右上角「＋ 加新」上传第一张照片，'
          + '或等家庭成员把照片共享到相册。</div>'
        : '<div class="empty">还没有观测记录。<br>去「扫描与识别」标签页跑一次扫描。</div>';
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
      ${photos.length ? `<div class="gal">${photos.slice(0, 24).map((x) =>
        `<img loading="lazy" src="${imgUrl(x.p.thumb_cache || x.p.image_path)}"
          title="${esc(x.o.obs_date)} ${esc(x.o.place_name || '')}" onclick="window.open(this.src)">`).join('')}</div>` : ''}
      <h2 style="margin-top:18px">观测记录</h2>
      ${d.observations.map((o) => `<div class="obs" data-obs="${o.id}">
        <span class="d">${esc(o.obs_date)}</span>
        <span class="p">${esc(o.place_name || '（无地点）')}</span>
        ${o.confidence != null ? `<span class="tiny"> 置信度 ${(o.confidence * 100).toFixed(0)}%</span>` : ''}
        ${o.notes ? `<div class="n">${esc(o.notes)}</div>` : ''}
        <button class="btn sm danger" style="margin-top:6px" onclick="B.deleteObs(${o.id}, this)">删除此观测</button>
      </div>`).join('')}`);
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

  async function deleteObs(obsId, btn) {
    if (!confirm('确认删除这条观测及其所有照片？')) return;
    btn.disabled = true;
    const r = await B.api('observation/' + obsId, { method: 'DELETE' }).then((x) => x.json());
    btn.closest('.obs').style.opacity = '0.3';
    btn.textContent = `已删除（${r.deleted_photos} 张照片）`;
    setTimeout(() => { B.closeModal(); load(); }, 800);
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
  B.openSpecies = openSpecies;
  B.deleteObs = deleteObs;
  B.bindWall = bind;
})(window.B);
