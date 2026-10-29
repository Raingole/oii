(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const storageKey = 'mmtr.observer.key.v1';
  const state = { key: '', result: null, session: '', event: '', mode: 'sentences', node: '', busy: false, request: 0, abort: null, signature: '' };
  try { state.key = sessionStorage.getItem(storageKey) || ''; } catch { /* Memory-only access still works. */ }
  const channelNames = { fact: '事实', gist: '感觉', affect: '情绪' };
  const sourceNames = { qq: 'QQ', esp32: '语音', sms: '短信', email: '邮件', controller: '工具回执' };
  const element = (name, className, text) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const short = (text, count = 34) => [...String(text)].length > count ? [...String(text)].slice(0, count).join('') + '…' : String(text);
  function formatDate(value, full = false) {
    const date = new Date(value);
    if (!Number.isFinite(+date)) return '时间未记录';
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', ...(full ? { second: '2-digit' } : {}) }).format(date);
  }
  function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
  function connection(text, connected = false) {
    $('connection').textContent = text;
    $('connection-dot').classList.toggle('connected', connected);
  }
  function showConnection() {
    $('token').value = state.key;
    $('connect-error').textContent = '';
    if (!$('connect-dialog').open) $('connect-dialog').showModal();
    $('token').focus();
  }
  function allNodes() {
    const selected = state.result?.selected;
    return selected ? [...selected.memory_nodes, ...selected.cognition_nodes] : [];
  }
  function showDetail(id) {
    state.node = id;
    const node = allNodes().find(value => value.id === id);
    const root = $('detail'); root.replaceChildren();
    document.querySelectorAll('[data-node]').forEach(button => {
      button.classList.toggle('selected', button.dataset.node === id);
      button.setAttribute('aria-pressed', String(button.dataset.node === id));
    });
    if (!node) { root.append(element('p', 'detail-empty', '点击一个关联词句，查看它为什么出现在本轮对话中。')); return; }
    const contentChannels = (node.channels || []).filter(value => value.content);
    const type = node.kind === 'cognition' ? node.label : node.memory_kind === 'core' ? '常驻记忆' : contentChannels.length === 1 ? '记忆 · ' + channelNames[contentChannels[0].channel] : node.label + ' · 多通道';
    root.append(element('span', 'source-tag' + (node.kind === 'cognition' ? ' cognitive' : ''), type));
    root.append(element('p', 'detail-quote', '“' + node.text + '”'));
    const list = element('dl', 'details-table');
    for (const [label, value] of [['关联原因', (node.reasons || []).join('、') || '实际上下文引用'], ['来源', node.source + (node.provided_to_model ? ' · 已供模型' : '')]]) {
      list.append(element('dt', '', label), element('dd', '', value));
    }
    root.append(list);
    if (node.channels?.length) {
      root.append(element('p', 'channel-title', '通道强度'));
      for (const channel of node.channels) {
        const row = element('div', 'channel');
        const track = element('div', 'track');
        const bar = element('i');
        const value = Math.max(0, Math.min(1, Number(channel.strength) || 0));
        bar.style.width = `${channel.present === false ? 0 : value * 100}%`; track.append(bar);
        row.append(element('span', '', channelNames[channel.channel] || channel.channel), track, element('small', '', channel.present === false ? '—' : value.toFixed(2)));
        root.append(row);
      }
    } else root.append(element('p', 'detail-empty', node.memory_kind === 'core' ? '常驻记忆不参与通道衰减。' : '此引用不使用记忆通道衰减。'));
  }
  function svgElement(name, attributes = {}) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }
  function renderGraph() {
    const root = $('graph'); root.replaceChildren();
    const selected = state.result?.selected;
    if (!selected) {
      const empty = element('div', 'graph-empty');
      empty.append(element('span', 'empty-orbit'), element('h3', '', '还没有本轮关联'), element('p', '', '对话发生后，实际召回的词句会出现在这里。'));
      root.append(empty); $('node-count').textContent = ''; showDetail(''); return;
    }
    const left = selected.memory_nodes || [], right = selected.cognition_nodes || [];
    const height = Math.max(350, Math.max(left.length, right.length) * 77 + 44);
    const svg = svgElement('svg', { viewBox: `0 0 760 ${height}`, role: 'group', 'aria-label': '对话关联图' });
    const positions = new Map();
    const place = (nodes, x) => nodes.map((node, index) => {
      const y = (index + 1) * height / (nodes.length + 1) - 31;
      positions.set(node.id, { x: x + 108, y: y + 31 });
      return { node, x, y };
    });
    const entries = [...place(left, 20), ...place(right, 524)];
    for (const edge of selected.connections || []) {
      const a = positions.get(edge.a), b = positions.get(edge.b);
      if (!a || !b) continue;
      svg.append(svgElement('path', { class: 'graph-line stored-edge', d: `M ${a.x} ${a.y} Q ${a.x - 80} ${(a.y + b.y) / 2} ${b.x} ${b.y}` }));
    }
    for (const item of entries) {
      const isCognition = item.node.kind === 'cognition';
      const start = isCognition ? 431 : 329;
      const end = isCognition ? item.x : item.x + 216;
      const cy = item.y + 31;
      const middle = (start + end) / 2;
      svg.append(svgElement('path', { class: 'graph-line' + (isCognition ? ' cognition-edge' : ''), d: `M ${start} ${height / 2} C ${middle} ${height / 2}, ${middle} ${cy}, ${end} ${cy}` }));
      const frame = svgElement('foreignObject', { x: item.x, y: item.y - 5, width: 216, height: 72 });
      const wrap = element('div', 'node-wrap');
      const button = element('button', 'graph-node' + (isCognition ? ' cognition-node' : ''));
      button.type = 'button'; button.dataset.node = item.node.id;
      button.title = item.node.text;
      button.setAttribute('aria-label', item.node.label + '：' + item.node.text);
      const text = state.mode === 'words' && item.node.terms?.length ? item.node.terms.join(' · ') : item.node.text;
      button.append(element('span', 'node-text', short(text, 40)));
      button.addEventListener('click', () => showDetail(item.node.id));
      wrap.append(button); frame.append(wrap); svg.append(frame);
    }
    const center = svgElement('foreignObject', { x: 323, y: height / 2 - 57, width: 114, height: 114 });
    const centerWrap = element('div', 'node-wrap');
    const queryTerms = left.flatMap(node => node.terms || []).filter(term => selected.input_text.includes(term));
    const query = queryTerms[0] || short(selected.input_text || selected.query || '本轮对话', 14);
    const centerNode = element('div', 'query-node', short(query, 18));
    centerNode.title = selected.input_text;
    centerWrap.append(centerNode); center.append(centerWrap); svg.append(center);
    root.append(svg);
    $('node-count').textContent = `${left.length} 条关联 · ${right.length} 条引用`;
    const selectedNode = allNodes().find(node => node.id === state.node) || left[0] || right[0];
    showDetail(selectedNode?.id || '');
  }
  function renderCognition() {
    const root = $('cognition-list'); root.replaceChildren();
    const nodes = state.result?.selected?.cognition_nodes || [];
    if (!nodes.length) { root.append(element('p', 'detail-empty', '尚无可展示的认知引用')); return; }
    for (const node of nodes) {
      const button = element('button', 'cognition-quote'); button.type = 'button';
      button.append(element('span', 'source-tag cognitive', node.label), element('p', '', '“' + node.text + '”'), element('small', '', node.source));
      button.addEventListener('click', () => { showDetail(node.id); if (window.innerWidth < 901) $('detail-title').scrollIntoView({ behavior: 'smooth', block: 'center' }); });
      root.append(button);
    }
  }
  function render(result) {
    state.result = result;
    if (!state.session) state.session = result.session_id;
    const select = $('session');
    const optionState = JSON.stringify(result.sessions.map(value => [value.id, value.latest_text]));
    if (select.dataset.options !== optionState) {
      select.replaceChildren();
      if (!result.sessions.length) select.append(new Option('当前会话', ''));
      for (const session of result.sessions) select.append(new Option(`${sourceNames[session.source] || session.source} · ${short(session.latest_text || session.id, 20)}`, session.id));
      select.dataset.options = optionState;
    }
    select.value = result.session_id;
    $('timeline').replaceChildren();
    for (const event of result.events) {
      const button = element('button', 'timeline-row' + (event.event_id === result.selected?.event_id ? ' active' : ''));
      button.type = 'button'; button.dataset.event = event.event_id;
      button.setAttribute('aria-current', String(event.event_id === result.selected?.event_id));
      const time = element('time', '', formatDate(event.timestamp)); time.dateTime = event.timestamp;
      button.append(time, element('span', 'snippet', event.input_text || '工具执行回执'));
      button.addEventListener('click', () => { state.event = event.event_id; load(); });
      $('timeline').append(button);
    }
    if (!result.events.length) $('timeline').append(element('p', 'rail-empty', '还没有对话记录。发送消息后，这里会自动更新。'));
    const selected = result.selected;
    $('current-text').textContent = selected ? '“' + (selected.input_text || selected.query) + '”' : '等待一段真实对话';
    $('current-meta').textContent = selected ? `${sourceNames[selected.source] || selected.source} · ${formatDate(selected.timestamp, true)} · ${selected.mode === 'llm' ? '模型决策' : '规则决策'}` : '';
    renderGraph(); renderCognition();
    if (result.backend !== 'mmtr') notice(`当前后端为 ${result.backend}，更新并重启中控后将优先使用 MMTR。`);
    else if (result.memory_degraded) notice('记忆接口当前异常，本轮关联可能不完整。');
    else if (selected?.omitted_reminders) notice('部分约定超出本轮上下文预算，未出现在当前关联中。');
    else notice();
  }
  async function load({ quiet = false } = {}) {
    if (!state.key) { connection('尚未连接'); if (!quiet) showConnection(); return false; }
    if (quiet && state.busy) return false;
    state.abort?.abort();
    const id = ++state.request;
    state.abort = new AbortController(); state.busy = true;
    const timeout = setTimeout(() => state.abort?.abort(), 12000);
    $('refresh').disabled = true;
    try {
      const query = new URLSearchParams();
      if (state.session) query.set('session_id', state.session);
      if (state.event) query.set('event_id', state.event);
      const response = await fetch('/api/memory/activity?' + query, { headers: { Authorization: 'Bearer ' + state.key }, cache: 'no-store', signal: state.abort.signal });
      if (id !== state.request) return false;
      if (response.status === 401) {
        connection('密钥未验证');
        if ($('connect-dialog').open) $('connect-error').textContent = '观察密钥不正确，请重新输入。';
        else if (!quiet) showConnection();
        return false;
      }
      if (response.status === 404 && state.event) { state.event = ''; state.busy = false; return load(); }
      if (!response.ok) throw new Error(response.status === 503 ? '思维核心尚未启用，请确认中控已启动。' : '暂时无法读取关联，请稍后刷新。');
      const result = await response.json();
      if (id !== state.request) return false;
      const signature = JSON.stringify([result.revision, result.session_id, result.selected?.event_id, result.backend, result.memory_degraded]);
      if (signature !== state.signature) { render(result); state.signature = signature; }
      $('updated').textContent = ($('auto').checked ? '已同步 ' : '已暂停自动刷新 · ') + new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date());
      connection(result.backend === 'mmtr' ? 'MMTR 已连接' : '观察台已连接', true);
      return true;
    } catch (error) {
      if (id === state.request && error.name !== 'AbortError') { connection('连接中断'); notice(error.message); $('connect-error').textContent = error.message; }
      return false;
    } finally {
      clearTimeout(timeout);
      if (id === state.request) { state.busy = false; $('refresh').disabled = false; }
    }
  }
  $('settings').addEventListener('click', showConnection);
  $('close-dialog').addEventListener('click', () => $('connect-dialog').close());
  $('refresh').addEventListener('click', () => load());
  $('session').addEventListener('change', event => { state.session = event.target.value; state.event = ''; state.node = ''; load(); });
  for (const mode of ['sentences', 'words']) $(mode).addEventListener('click', () => {
    state.mode = mode;
    for (const id of ['sentences', 'words']) $(id).setAttribute('aria-pressed', String(id === mode));
    renderGraph();
  });
  $('auto').addEventListener('change', () => { if ($('auto').checked) load(); else $('updated').textContent = '已暂停自动刷新'; });
  $('connect-form').addEventListener('submit', async event => {
    event.preventDefault(); state.key = $('token').value.trim(); state.signature = '';
    $('connect-error').textContent = '';
    if (await load()) { try { sessionStorage.setItem(storageKey, state.key); } catch { /* Keep key in memory only. */ } $('connect-dialog').close(); }
  });
  $('disconnect').addEventListener('click', () => {
    state.abort?.abort(); state.request++; state.busy = false; state.key = ''; state.result = null; state.session = ''; state.event = ''; state.signature = '';
    try { sessionStorage.removeItem(storageKey); } catch { /* No persisted key. */ }
    render({ sessions: [], events: [], selected: null, session_id: '', backend: 'mmtr' });
    $('token').value = ''; $('updated').textContent = '等待连接'; $('refresh').disabled = false; connection('尚未连接'); $('connect-dialog').close();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && $('auto').checked) load({ quiet: true }); });
  setInterval(() => { if ($('auto').checked && !document.hidden && state.key) load({ quiet: true }); }, 2500);
  load();
})();
