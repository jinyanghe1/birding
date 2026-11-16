/* species_picker.js — 物种自动补全 + 修正对话框
 *
 * 核心目的：修正时输入任意叫法（玄凤 / 玄凤鹦鹉 / 鸡尾鹦鹉 / Cockatiel）
 * 都归一到库内同一物种，杜绝同鸟多名。
 */
(function (B) {
  const { $, esc, api, apiJSON } = B;

  /** 给 input 挂自动补全：选中后 input.dataset.speciesId 为规范物种 id。 */
  function attachSpeciesPicker(input, onSelect) {
    const wrap = document.createElement('div');
    wrap.className = 'spWrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const list = document.createElement('div');
    list.className = 'spList';
    list.style.display = 'none';
    wrap.appendChild(list);

    let items = [], timer = null;
    const close = () => { list.style.display = 'none'; };

    function choose(d) {
      input.value = d.common_name_cn;
      input.dataset.speciesId = d.id;
      close();
      if (onSelect) onSelect(d);
    }

    input.addEventListener('input', () => {
      input.dataset.speciesId = '';      // 一改字就失效，防止沿用旧选择
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { close(); return; }
      timer = setTimeout(async () => {
        items = await apiJSON('species/search?q=' + encodeURIComponent(q)).catch(() => []);
        if (!items.length) {
          list.innerHTML = `<div class="spItem spNew">库内无匹配，确认后将新建物种「${esc(q)}」</div>`;
          list.style.display = 'block';
          return;
        }
        list.innerHTML = items.map((d, i) => `<div class="spItem" data-i="${i}">
          <div><b>${esc(d.common_name_cn)}</b>
            ${d.obs_n ? `<span class="spCnt">${d.obs_n} 次观测</span>` : '<span class="spCnt">库内无观测</span>'}
          </div>
          <div class="spSci">${esc(d.scientific_name || d.common_name_en || '')}</div>
          ${d.matched_alias && d.matched_alias !== d.common_name_cn
            ? `<div class="spAlias">输入的「${esc(d.matched_alias)}」→ 归一到 ${esc(d.common_name_cn)}</div>` : ''}
          ${d.aliases && d.aliases.length
            ? `<div class="spAlias">别名：${esc(d.aliases.join(' / '))}</div>` : ''}
        </div>`).join('');
        list.style.display = 'block';
        list.querySelectorAll('.spItem[data-i]').forEach(
          (el) => { el.onclick = () => choose(items[+el.dataset.i]); });
      }, 200);
    });
    document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
    return { close };
  }

  /** 修正对话框：选/填正确鸟种 → 调 /api/review/reassign → onDone(结果)。 */
  function openReassignDialog(obsId, currentName, onDone) {
    const old = $('#reassignDialog');
    if (old) old.remove();
    const dlg = document.createElement('div');
    dlg.id = 'reassignDialog';
    dlg.className = 'spModalMask';
    dlg.innerHTML = `<div class="spModal">
      <h3>修正鸟种</h3>
      <div class="tiny" style="margin-bottom:8px">当前识别：<b>${esc(currentName || '未知')}</b></div>
      <input id="reassignInput" placeholder="输入中文名 / 别名 / 英文名 / 拉丁名"
             autocomplete="off" style="width:100%">
      <div class="tiny" style="margin:8px 0">输入后从下拉中选择，别名会自动归一到同一物种。</div>
      <div class="act">
        <button class="btn" id="reassignCancel">取消</button>
        <button class="btn primary" id="reassignOk">确认修正</button>
      </div>
      <div id="reassignMsg" class="tiny" style="margin-top:8px"></div>
    </div>`;
    document.body.appendChild(dlg);
    const input = dlg.querySelector('#reassignInput');
    attachSpeciesPicker(input);
    input.focus();
    const close = () => dlg.remove();
    dlg.querySelector('#reassignCancel').onclick = close;
    dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });

    dlg.querySelector('#reassignOk').onclick = async (ev) => {
      const btn = ev.target;
      const name = input.value.trim();
      if (!name) { dlg.querySelector('#reassignMsg').textContent = '请先输入鸟种名'; return; }
      btn.disabled = true;
      const qs = input.dataset.speciesId
        ? `species_id=${input.dataset.speciesId}`
        : `new_species_cn=${encodeURIComponent(name)}`;
      const r = await api(`review/reassign?obs_id=${obsId}&${qs}`, { method: 'POST' })
        .then((x) => x.json()).catch((e) => ({ error: String(e) }));
      if (r.error) {
        btn.disabled = false;
        dlg.querySelector('#reassignMsg').textContent = '修正失败：' + r.error;
        return;
      }
      close();
      if (onDone) onDone(r);
    };
  }

  B.attachSpeciesPicker = attachSpeciesPicker;
  B.openReassignDialog = openReassignDialog;
})(window.B);
