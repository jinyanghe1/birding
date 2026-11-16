/* map.js — Leaflet 地图（OSM 优先，百度 fallback） */
(function (B) {
  const { $, esc, apiJSON, imgUrl } = B;
  let MAP = null, LAYER = null, TOKEN = 0, HOME = null;

  async function initMap() {
    if (MAP) { MAP.invalidateSize(); return; }
    // 常住地决定默认视野（默认深圳，可在推荐页切换城市），不再硬编码中国全景
    HOME = await apiJSON('config/home').catch(() => null);
    MAP = L.map('mapContainer', { zoomControl: true, attributionControl: true, worldCopyJump: true });
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 19, subdomains: ['a', 'b', 'c'],
    });
    osm.on('tileerror', async () => {
      if (MAP._fellback) return;
      MAP._fellback = true;
      MAP.removeLayer(osm);
      let ak = '';
      try { ak = (await apiJSON('config/baidu-map-ak')).ak || ''; } catch (e) {}
      L.tileLayer('https://maponline{s}.bdimg.com/tile/?qt=tile&x={x}&y={y}&z={z}&styles=pl&scaler=1&udt=20191119&ak=' + ak, {
        attribution: '&copy; 百度地图', maxZoom: 18, subdomains: ['0', '1', '2', '3'],
      }).addTo(MAP);
    });
    osm.addTo(MAP);
    LAYER = L.layerGroup().addTo(MAP);
    MAP.on('zoomend moveend', loadMap);
    MAP.setView(HOME ? [HOME.lat, HOME.lon] : [22.5431, 114.0579], 11);
    addMapControls();
    setTimeout(() => { MAP.invalidateSize(); loadMap(); }, 120);
  }

  /** 地图右上：定位 / 回常住地 / 全部观测 */
  function addMapControls() {
    const ctl = L.control({ position: 'topright' });
    ctl.onAdd = function () {
      const div = L.DomUtil.create('div', 'mapCtl');
      div.innerHTML = `<button class="btn sm" id="btnLocate" title="浏览器定位">📍 定位</button>
        <button class="btn sm" id="btnHome" title="回到常住地">🏠 ${esc(HOME ? HOME.city : '深圳')}</button>
        <button class="btn sm" id="btnAll" title="缩放以显示全部观测点">全部观测</button>`;
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    ctl.addTo(MAP);
    $('#btnLocate').onclick = locateMe;
    $('#btnHome').onclick = () => HOME && MAP.setView([HOME.lat, HOME.lon], 11);
    $('#btnAll').onclick = fitAll;
  }

  function locateMe() {
    if (!navigator.geolocation) { B.toast('浏览器不支持定位', true); return; }
    navigator.geolocation.getCurrentPosition((pos) => {
      const { latitude, longitude } = pos.coords;
      MAP.setView([latitude, longitude], 13);
      L.circleMarker([latitude, longitude], { radius: 8, color: '#2f6fed' })
        .addTo(MAP).bindTooltip('我的位置').openTooltip();
    }, (err) => B.toast('定位失败：' + err.message, true), { timeout: 8000 });
  }

  async function fitAll() {
    const pts = await apiJSON(`map?zoom=4&min_conf=${$('#mc').value}`).catch(() => []);
    if (pts.length) MAP.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.25));
  }

  async function loadMap() {
    if (!MAP) return;
    const token = ++TOKEN;
    const z = Math.max(2, Math.min(9, MAP.getZoom()));
    const pts = await apiJSON(`map?zoom=${z}&min_conf=${$('#mc').value}`).catch(() => []);
    if (token !== TOKEN) return;
    LAYER.clearLayers();
    const maxN = Math.max(1, ...pts.map((p) => p.n));
    pts.forEach((p) => {
      const big = p.n >= Math.max(4, maxN * 0.4);
      const size = big ? 56 : p.n > 1 ? 46 : 36;
      const icon = L.divIcon({
        className: '',
        html: `<div style="position:relative;width:${size}px;height:${size}px">
              ${p.thumb ? `
                <img src="${imgUrl(p.thumb)}" style="width:100%;height:100%;border-radius:8px;
                  object-fit:cover;border:2px solid rgba(255,255,255,.9);box-shadow:0 2px 8px rgba(0,0,0,.4)">
                ${p.n > 1 ? `<div style="position:absolute;top:-4px;right:-4px;width:20px;height:20px;
                  border-radius:50%;background:var(--teal);color:#fff;font:600 11px/20px system-ui;
                  text-align:center;border:2px solid #fff">${p.n}</div>` : ''}
              ` : `<div style="width:100%;height:100%;border-radius:50%;background:var(--teal);
                  display:flex;align-items:center;justify-content:center;color:#fff;
                  font:600 12px system-ui;border:2px solid rgba(255,255,255,.9)">${p.n}</div>`}
            </div>`,
        iconSize: [size, size], iconAnchor: [size / 2, size / 2],
      });
      const m = L.marker([p.lat, p.lon], { icon }).addTo(LAYER);
      m.on('click', () => openCell(p, z));
    });
  }

  async function openCell(p, z) {
    const panel = $('#cellPanel');
    const head = `<h3>${esc(p.top_species || '观测点')} <span class="close" onclick="document.getElementById('cellPanel').style.display='none'">&times;</span></h3>
      <div class="m">${p.n} 次观测 · ${p.sp} 种 · 最近 ${esc((p.last_date || '').slice(0, 10))}</div>`;
    panel.style.display = 'block';
    panel.innerHTML = head + '<div class="tiny">加载中…</div>';
    const items = await apiJSON(
      `map/cell?lat=${p.lat}&lon=${p.lon}&zoom=${z + 2}&min_conf=${$('#mc').value}`).catch(() => []);
    panel.innerHTML = head + (items.length ? items.map((c) => `<div class="cellItem" onclick="B.openSpecies(${c.species_id})">
      ${c.thumb ? `<img src="${imgUrl(c.thumb)}">` : '<div style="width:52px;height:52px;border-radius:7px;background:var(--panel2)"></div>'}
      <div><div class="t">${esc(c.common_name_cn)}</div>
        <div class="m">${esc(c.obs_date)} · ${(c.confidence * 100).toFixed(0)}%${c.place_name ? ' · ' + esc(c.place_name.split(',')[0]) : ''}</div></div>
    </div>`).join('') : '<div class="tiny">该网格内没有更多记录</div>');
  }

  B.registerTab('map', { show: () => { initMap(); setTimeout(() => MAP && MAP.invalidateSize(), 60); } });
  B.reloadMap = () => { loadMap(); };
  B.locateMe = locateMe;
})(window.B);
