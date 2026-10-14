/* review.js — 「不是我的鸟？」存疑样本复核工作流 */
(function (B) {
  const { $, esc, api, apiJSON, imgUrl } = B;

  async function loadReview() {
    const items = await apiJSON('review/suspect?limit=50').catch(() => []);
    if (!items.length) {
      $('#reviewList').innerHTML = '<div class="empty">没有存疑样本了。</div>';
      return;
    }
    $('#reviewList').innerHTML = items.map((it) => {
      const suspect = it.in_china === 0 ? '⚠️ 不在中国名录' : `置信度 ${(it.confidence * 100).toFixed(0)}%`;
      return `<div class="reviewItem" data-obs="${it.obs_id}">
        <img src="${imgUrl(it.thumb || it.img)}" onclick="window.open('${imgUrl(it.img)}')" onerror="this.style.display='none'">
        <div class="info">
          <div class="nm">${esc(it.common_name_cn)} <span class="conf">${suspect}</span></div>
          <div class="meta">${esc(it.obs_date)} ${esc(it.obs_time || '')} · ${esc(it.place_name || '（无地点）')} · ${esc(it.identified_by || '')}</div>
          <div class="acts">
            <button class="danger" onclick="B.markNotBird(${it.obs_id}, this)">不是鸟</button>
            <button onclick="B.showReassign(${it.obs_id}, this)">分类错误</button>
            <button onclick="B.keepSample(${it.obs_id}, this)">保留</button>
          </div>
          <div class="reassignBox" style="display:none">
            <input placeholder="正确的中文名，例如：夜鹭" id="reassign-${it.obs_id}">
            <button class="primary" onclick="B.doReassign(${it.obs_id}, this)">确认</button>
          </div>
        </div>
      </div>`;
    }).join('');
  }

  async function markNotBird(obsId, btn) {
    if (!confirm('确认标记「不是鸟」？该观测及其照片将被删除。')) return;
    btn.disabled = true;
    const r = await api(`review/not-bird?obs_id=${obsId}`, { method: 'POST' }).then((x) => x.json());
    btn.closest('.reviewItem').style.opacity = '0.3';
    btn.closest('.reviewItem').querySelector('.meta').innerHTML +=
      ` <span style="color:var(--red)">已删除（${r.deleted_photos} 张照片）</span>`;
  }

  function showReassign(obsId, btn) {
    const box = btn.closest('.reviewItem').querySelector('.reassignBox');
    box.style.display = box.style.display === 'none' ? 'flex' : 'none';
  }

  async function doReassign(obsId, btn) {
    const input = $(`#reassign-${obsId}`);
    const name = input.value.trim();
    if (!name) { B.toast('请输入正确的中文名', true); return; }
    btn.disabled = true;
    await api(`review/reassign?obs_id=${obsId}&new_species_cn=${encodeURIComponent(name)}`, { method: 'POST' });
    btn.closest('.reviewItem').style.opacity = '0.3';
    btn.closest('.reviewItem').querySelector('.meta').innerHTML +=
      ` <span style="color:var(--teal)">已改到「${esc(name)}」</span>`;
  }

  function keepSample(obsId, btn) {
    btn.closest('.reviewItem').style.opacity = '0.3';
    btn.closest('.reviewItem').querySelector('.meta').innerHTML +=
      ' <span style="color:var(--tx3)">已保留</span>';
  }

  B.registerTab('review', { show: loadReview });
  B.markNotBird = markNotBird;
  B.showReassign = showReassign;
  B.doReassign = doReassign;
  B.keepSample = keepSample;
})(window.B);
