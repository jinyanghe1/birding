/* rec.js — 推荐清单：按城市 / 浏览器定位，eBird 社区热点 - 我的观测 */
(function (B) {
  const { $, esc, apiJSON } = B;
  const CJK = /[一-鿿]/;
  let HOME = null;
  let GEO = null;           // {lat, lon} 定位模式；null 表示按城市
  let LAST = [];            // 最近一次推荐结果，切换名称显示时不必重新拉取

  function displayName(r) {
    const t = $('#recNameType').value;
    if (t === 'en') return r.sciName || r.species_en || r.species;
    if (t === 'en_com') return r.species_en || r.species;
    // 中文模式：只有当中文名真的是中文时才用，否则回退英文名并标注
    const cn = r.species || '';
    if (CJK.test(cn)) return cn;
    return (r.species_en || cn) + '（暂无中文名）';
  }

  function render() {
    if (!LAST.length) {
      $('#recList').innerHTML = '<div class="empty">暂无推荐（社区数据加载失败或你已看过所有鸟种）。</div>';
      return;
    }
    $('#recList').innerHTML = LAST.map((r) => {
      const priority = r.priority === 'high' ? '🔥' : '·';
      return `<div class="reviewItem">
        <div class="info">
          <div class="nm">${priority} ${esc(displayName(r))}
            <span class="conf">社区 ${r.community_count} 次</span></div>
          <div class="meta">${esc(r.sciName || '')} · ${(r.locations || []).map(esc).join('、')}</div>
          <div class="meta" style="color:var(--tx3)">最近观测：${(r.dates || []).map(esc).join('、')}</div>
        </div>
      </div>`;
    }).join('');
  }

  async function loadRecommendations() {
    $('#recList').innerHTML = '<div class="tiny">加载中…</div>';
    const url = GEO
      ? `recommendations?lat=${GEO.lat}&lon=${GEO.lon}`
      : `recommendations?city=${encodeURIComponent($('#recCity').value || '')}`;
    LAST = await apiJSON(url).catch(() => []);
    $('#recLocLabel').textContent = GEO
      ? `你身边 50km 内（${GEO.lat.toFixed(3)}, ${GEO.lon.toFixed(3)}）最近 30 天的常见鸟种，减去你已看过的。`
      : `${$('#recCity').value}观鸟者最近 30 天最常看到的鸟种（eBird 社区数据），减去你已看过的。按社区观测次数排序。`;
    render();
  }

  async function bind() {
    HOME = await apiJSON('config/home').catch(() => null);
    const sel = $('#recCity');
    const cities = (HOME && HOME.cities) || ['深圳', '北京', '广州', '上海'];
    sel.innerHTML = cities.map((c) =>
      `<option value="${esc(c)}" ${HOME && c === HOME.city ? 'selected' : ''}>${esc(c)}</option>`).join('');
    sel.onchange = () => { GEO = null; loadRecommendations(); };
    $('#recNameType').onchange = render;
    $('#recLocate').onclick = () => {
      if (!window.isSecureContext) {
        $('#recLocLabel').textContent = '当前 HTTP 页面不允许浏览器定位，请改用城市选择；HTTPS 开启后可定位。';
        B.toast('HTTP 页面无法定位，请使用城市选择', true);
        return;
      }
      if (!navigator.geolocation) { B.toast('浏览器不支持定位', true); return; }
      $('#recLocLabel').textContent = '定位中…';
      navigator.geolocation.getCurrentPosition((pos) => {
        GEO = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        loadRecommendations();
      }, (err) => B.toast('定位失败：' + err.message, true), { timeout: 8000 });
    };
  }

  B.registerTab('rec', { show: loadRecommendations });
  B.bindRec = bind;
})(window.B);
