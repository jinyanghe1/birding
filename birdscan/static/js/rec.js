/* rec.js — 推荐清单（eBird 社区热点 - 我的观测） */
(function (B) {
  const { $, esc, apiJSON } = B;

  async function loadRecommendations() {
    const rec = await apiJSON('recommendations').catch(() => []);
    if (!rec.length) {
      $('#recList').innerHTML = '<div class="empty">暂无推荐（社区数据加载失败或你已看过所有鸟种）。</div>';
      return;
    }
    const nameType = $('#recNameType').value;
    $('#recList').innerHTML = rec.map((r) => {
      const priority = r.priority === 'high' ? '🔥' : '·';
      const name = nameType === 'cn' ? r.species
        : nameType === 'cn_com' ? (r.species || r.species_en)
        : nameType === 'en' ? r.sciName
        : r.species_en;
      return `<div class="reviewItem">
        <div class="info">
          <div class="nm">${priority} ${esc(name)} <span class="conf">社区 ${r.community_count} 次</span></div>
          <div class="meta">${esc(r.sciName || '')} · ${r.locations.map(esc).join('、')}</div>
          <div class="meta" style="color:var(--tx3)">最近观测：${r.dates.map(esc).join('、')}</div>
        </div>
      </div>`;
    }).join('');
  }

  function bind() { $('#recNameType').onchange = loadRecommendations; }

  B.registerTab('rec', { show: loadRecommendations });
  B.bindRec = bind;
})(window.B);
