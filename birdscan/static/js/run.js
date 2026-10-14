/* run.js — 手动扫描 + 识别队列 */
(function (B) {
  const { $, esc, api, apiJSON } = B;

  async function loadQueue() {
    const q = await apiJSON('queue?limit=24').catch(() => []);
    $('#qlist').innerHTML = q.length
      ? `<div>待识别 <b style="color:var(--amber)">${q.length}</b> 张（最多显示 24）</div>` +
        q.map((x) => `<div style="margin-top:4px">· ${esc((x.shot_at || '').slice(0, 10))}
          ${esc(x.place_name || '')} <span style="color:var(--tx3)">${esc((x.asset_uuid || '').slice(0, 8))}</span></div>`).join('')
      : '<div>队列为空。跑一次扫描，或在对话里说「继续识别」。</div>';
  }

  function bind() {
    $('#btnScan').onclick = async () => {
      const lim = $('#lim').value ? '&limit=' + $('#lim').value : '';
      const frc = $('#frc').checked ? '&force=true' : '';
      const out = $('#scanOut');
      out.style.display = 'block';
      out.textContent = '扫描中…（全库可能需要十几分钟，请不要关闭页面）';
      $('#btnScan').disabled = true;
      try {
        const r = await api('scan?' + (lim + frc).replace(/^&/, ''), { method: 'POST' });
        const j = await r.json();
        out.textContent = JSON.stringify(j, null, 2);
        if (B.reloadWall) B.reloadWall();
      } catch (e) { out.textContent = '失败：' + e.message; }
      $('#btnScan').disabled = false;
    };
  }

  B.registerTab('run', { show: loadQueue });
  B.bindRun = bind;
  B.reloadWall = () => B.switchTab('wall');
})(window.B);
