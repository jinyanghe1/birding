/* viz.js — 可视化：热力日历 / 累计增长 / 地点 / 时段 / 迁徙日历 */
(function (B) {
  const { $, esc, apiJSON } = B;

  async function loadViz() {
    const y = new Date().getFullYear();
    const [cal, tl, pl, hr] = await Promise.all([
      apiJSON(`calendar?year=${y}`).catch(() => ({ days: [] })),
      apiJSON('timeline').catch(() => []),
      apiJSON('places').catch(() => []),
      apiJSON('hours').catch(() => []),
    ]);

    // 热力日历（按月翻页）
    let calYear = new Date().getFullYear();
    let calMonth = new Date().getMonth() + 1;
    function renderCal() {
      $('#calY').textContent = `${calYear} 年 ${calMonth} 月`;
      const map = {};
      (cal.days || []).forEach((d) => (map[d.date] = d.count));
      const first = new Date(calYear, calMonth - 1, 1);
      const last = new Date(calYear, calMonth, 0);
      const off = first.getDay();
      let cells = '';
      for (let i = 0; i < off; i++) cells += '<i class="d"></i>';
      for (let d = new Date(first); d <= last; d.setDate(d.getDate() + 1)) {
        const iso = d.toISOString().slice(0, 10);
        const c = map[iso] || 0;
        const bg = c === 0 ? '#e8e8ed' : c === 1 ? '#c7e5ff' : c === 2 ? '#7cc4ff' : '#007aff';
        cells += `<i style="background:${bg}" title="${iso}: ${c} 次"></i>`;
      }
      $('#cal').innerHTML = cells;
    }
    $('#calPrev').onclick = () => { calMonth--; if (calMonth < 1) { calMonth = 12; calYear--; } renderCal(); };
    $('#calNext').onclick = () => { calMonth++; if (calMonth > 12) { calMonth = 1; calYear++; } renderCal(); };
    renderCal();

    // 累计增长曲线
    if (tl.length) {
      const W = 380, H = 150, P = 26;
      const maxN = tl[tl.length - 1].cumulative || 1;
      const t0 = new Date(tl[0].date).getTime();
      const t1 = new Date(tl[tl.length - 1].date).getTime() || t0 + 1;
      const pts = tl.map((p) => {
        const x = P + (new Date(p.date).getTime() - t0) / (t1 - t0) * (W - 2 * P);
        const yy = H - P - p.cumulative / maxN * (H - 2 * P);
        return `${x.toFixed(1)},${yy.toFixed(1)}`;
      }).join(' ');
      $('#tl').innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="150">
        <polyline points="${pts}" fill="none" stroke="var(--teal)" stroke-width="1.8"/>
        <text x="${P}" y="14" fill="#6b7382" font-size="10">${maxN} 种</text>
        <text x="${P}" y="${H - 6}" fill="#6b7382" font-size="10">${esc(tl[0].date)}</text>
        <text x="${W - P}" y="${H - 6}" fill="#6b7382" font-size="10" text-anchor="end">${esc(tl[tl.length - 1].date)}</text>
      </svg>`;
    } else $('#tl').innerHTML = '<div class="tiny">暂无数据</div>';

    // 地点
    const mx = Math.max(1, ...pl.map((p) => p.c));
    $('#pl').innerHTML = pl.length ? pl.map((p) => `<div class="bar">
      <span class="nm" title="${esc(p.place_name)}">${esc(p.place_name)}</span>
      <span class="tk"><span class="fl" style="width:${(p.c / mx) * 100}%"></span></span>
      <span class="vl">${p.c}次/${p.sp}种</span></div>`).join('')
      : '<div class="tiny">暂无地点数据</div>';

    // 时段
    const mh = Math.max(1, ...hr);
    $('#hr').innerHTML = `<div class="hr">${hr.map((v, i) =>
      `<div style="height:${(v / mh) * 100}%" title="${i}点: ${v} 次"></div>`).join('')}</div>
      <div class="hrl">${hr.map((v, i) => `<span>${i % 3 === 0 ? i : ''}</span>`).join('')}</div>`;

    // 迁徙日历
    const mig = await apiJSON('migration').catch(() => []);
    const now = new Date().getMonth() + 1;
    const bySpecies = {};
    mig.forEach((r) => {
      if (!bySpecies[r.species_id]) bySpecies[r.species_id] = { name: r.common_name_cn, months: [] };
      bySpecies[r.species_id].months.push({ m: r.month, y: r.years, c: r.count });
    });
    $('#mig').innerHTML = Object.entries(bySpecies).map(([sid, s]) => {
      const months = Array(12).fill(0);
      s.months.forEach(({ m, c }) => { months[m - 1] = c; });
      const nz = months.filter((x) => x > 0);
      const avg = nz.reduce((a, b) => a + b, 0) / Math.max(1, nz.length);
      const lastSeen = Math.max(...s.months.map((x) => x.m));
      const overdue = lastSeen < now - 2 && avg > 0;
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-top:1px solid var(--line)">
        <div style="width:80px;font-size:12px;${overdue ? 'color:var(--amber);font-weight:500' : ''}">${esc(s.name)}${overdue ? ' 🔥' : ''}</div>
        <div style="display:flex;gap:2px;flex:1">${months.map((c, i) => {
          const m = i + 1;
          const bg = c === 0 ? 'var(--panel2)' : c >= avg ? 'var(--teal)' : '#7cc4ff';
          const hl = m === now ? 'outline:1.5px solid var(--amber)' : '';
          return `<div style="flex:1;height:18px;border-radius:3px;background:${bg};${hl}"
            title="${m}月: ${c} 次${overdue && m === now ? '（该出门了）' : ''}"></div>`;
        }).join('')}</div>
        <div style="width:60px;font-size:11px;color:var(--tx3)">${s.months.length} 个月</div>
      </div>`;
    }).join('') || '<div class="tiny">暂无迁徙数据</div>';
  }

  B.registerTab('viz', { show: loadViz });
  B.reloadViz = loadViz;
})(window.B);
