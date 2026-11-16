/* review.js — 待复核：删除（二次确认）/ 修正（自动补全归一）/ 保留 */
(function (B) {
  const { $, esc, apiJSON } = B;

  async function loadReview() {
    const items = await apiJSON('review/suspect?limit=50').catch(() => []);
    if (!items.length) {
      $('#reviewList').innerHTML = '<div class="empty">没有待复核的样本 🎉</div>';
      return;
    }
    $('#reviewList').innerHTML = items.map((it) => {
      const suspect = it.in_china === 0
        ? '⚠️ 不在中国名录'
        : `置信度 ${(it.confidence * 100).toFixed(0)}%`;
      return `<div class="reviewItem" data-obs="${it.obs_id}"
                  data-name="${esc(it.common_name_cn)}">
      <img src="${B.imgUrl(it.thumb || it.img)}"
           onclick="window.open('${B.imgUrl(it.img)}')"
           onerror="this.style.display='none'">
      <div class="info">
        <div class="nm">${esc(it.common_name_cn)}
          <span class="conf">${suspect}</span></div>
        <div class="meta">${esc(it.obs_date)} ${esc(it.obs_time || '')}
          · ${esc(it.place_name || '（无地点）')}
          · ${esc(it.identified_by || '')}</div>
        ${it.notes ? `<div class="meta">${esc(it.notes)}</div>` : ''}
        <div class="acts">
          <button class="danger" onclick="B.deleteObs(${it.obs_id}, this)">删除</button>
          <button onclick="B.reassignObs(${it.obs_id}, this)">修正</button>
          <button onclick="B.keepSample(${it.obs_id}, this)">保留</button>
        </div>
      </div>
    </div>`;
    }).join('');
  }

  /** 删除键：二次确认后物理删除观测与照片。onDone 可选（详情页用于关弹窗刷新）。 */
  async function deleteObs(obsId, btn, onDone) {
    const card = btn.closest('[data-obs]');
    const name = (card && card.dataset.name) || '该记录';
    if (!confirm(`确认删除「${name}」这条观测？\n\n该观测及其全部照片将从数据库中删除（每日备份仍可找回）。`)) return;
    if (!confirm(`再确认一次：真的要删除「${name}」吗？此操作不可撤销。`)) return;
    btn.disabled = true;
    const r = await B.api(`review/not-bird?obs_id=${obsId}`, { method: 'POST' })
      .then((x) => x.json()).catch((e) => ({ error: String(e) }));
    if (r.error) { btn.disabled = false; B.toast('删除失败：' + r.error, true); return; }
    if (card) {
      card.style.opacity = '0.35';
      const acts = card.querySelector('.acts');
      if (acts) acts.innerHTML = `<span class="tiny">已删除（${r.deleted_photos ?? 0} 张照片）</span>`;
    }
    if (B.reloadStats) B.reloadStats();
    if (onDone) onDone(r);
  }

  /** 修正键：自动补全选择正确鸟种，别名自动归一。 */
  function reassignObs(obsId, btn, onDone) {
    const card = btn.closest('[data-obs]');
    const name = (card && card.dataset.name) || '';
    B.openReassignDialog(obsId, name, (r) => {
      if (card) {
        card.style.opacity = '0.6';
        const nm = card.querySelector('.nm');
        if (nm) nm.innerHTML = `${esc(r.new_species)} <span class="conf">已修正</span>`;
        const acts = card.querySelector('.acts');
        if (acts) acts.innerHTML =
          `<span class="tiny">已修正为 ${esc(r.new_species)}${r.merged_into ? '（已合并同日观测）' : ''}</span>`;
      }
      if (B.reloadStats) B.reloadStats();
      if (onDone) onDone(r);
    });
  }

  function keepSample(obsId, btn) {
    const card = btn.closest('.reviewItem');
    if (card) card.style.opacity = '0.35';
    btn.textContent = '已保留';
  }

  B.registerTab('review', { show: loadReview });
  B.loadReview = loadReview;
  B.deleteObs = deleteObs;
  B.reassignObs = reassignObs;
  B.keepSample = keepSample;
})(window.B);
