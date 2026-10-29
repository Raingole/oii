(() => {
  'use strict';
  const $=id=>document.getElementById(id), storageKey='mmtr.observer.key.v1';
  const state={key:'',snapshot:null,selected:'',filter:'all',busy:false};
  try{state.key=sessionStorage.getItem(storageKey)||'';}catch{}
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const time=value=>{const date=new Date(Number(value)*1000);return Number.isFinite(+date)?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(date):'时间未记录';};
  const statusName={pending:'待确认',approved:'已批准',running:'运行中',succeeded:'已完成',failed:'执行失败',rejected:'已取消',expired:'已过期',superseded:'已替代',interrupted:'已中断'};
  const stageName={request_created:'OII 创建请求',approved:'用户确认',rejected:'用户取消',expired:'确认码过期',superseded:'请求被替代',execution_started:'OII 启动任务',input_prepared:'OII 组装输入',process_spawned:'DSH 进程已启动',execution_succeeded:'DSH 执行完成',execution_failed:'DSH 执行失败',execution_interrupted:'DSH 任务中断',install_started:'开始安装 DSH',qq_delivered:'QQ 消息已送达',qq_failed:'QQ 投递失败',qq_unavailable:'没有 QQ 发送器'};
  function notice(message=''){const box=$('notice');box.textContent=message;box.hidden=!message;}
  function setHealth(id,text,mode='neutral'){const node=$(id);node.textContent=text;const dot=$(id==='record-count'?'records-dot':id.replace(/state|runtime|count/,'dot'));if(dot)dot.className='dot '+mode;}
  function showAuth(){ $('token').value=state.key;$('auth-error').textContent='';if(!$('auth-dialog').open)$('auth-dialog').showModal();$('token').focus(); }
  function requests(){return state.snapshot?.requests||[];}
  function visible(){return requests().filter(row=>state.filter==='all'||row.status===state.filter);}
  function current(){return requests().find(row=>row.id===state.selected)||visible()[0]||null;}
  function stateClass(value){return ['pending','approved','running','succeeded','failed','rejected','expired','superseded','interrupted'].includes(value)?value:'';}
  function createTimeline(row){
    if(row.events?.length)return row.events;
    const fallback=[{stage:'request_created',created:row.created,detail:'历史请求记录（未保存逐步事件）。'}];
    if(row.approved)fallback.push({stage:'approved',created:row.approved,detail:'该任务已获得一次性确认。'});
    if(row.process_started)fallback.push({stage:'process_spawned',created:row.process_started,detail:'DSH 子进程已启动。'});
    if(row.finished)fallback.push({stage:'execution_'+(row.status==='succeeded'?'succeeded':row.status),created:row.finished,detail:row.status==='succeeded'?'任务结束。':String(row.result||'任务未成功结束。').slice(0,500)});
    if(row.notification_at)fallback.push({stage:'qq_'+row.notification_status,created:row.notification_at,detail:row.notification_error||''});
    return fallback;
  }
  function renderRequests(){
    const root=$('requests');root.replaceChildren();const rows=visible();
    if(!rows.length){root.append(el('div','empty small',state.snapshot?(requests().length?'没有符合筛选条件的请求。':'尚无 DSH 请求记录；请求和状态只会在真实发生后出现。'):'连接观察台后显示真实 DSH 请求。'));return;}
    for(const row of rows){
      const button=el('button','request-row'+(row.id===state.selected?' selected':''));button.type='button';
      const date=el('span','request-date');date.append(el('time','',time(row.created)),el('span','state '+stateClass(row.status),statusName[row.status]||row.status));
      const title=row.operation==='install_runtime'?'安装 DSH 运行时':(row.task||'DSH 任务').replace(/\s+/g,' ').slice(0,72);
      button.append(date,el('strong','',title),el('span','snippet',row.operation==='install_runtime'?'首次安装请求':String(row.rationale||row.task||'').replace(/\s+/g,' ').slice(0,115)));
      button.addEventListener('click',()=>{state.selected=row.id;renderRequests();renderDetail();});root.append(button);
    }
  }
  function panel(title,text,kind=''){
    const box=el('section','log-panel'+(kind?' '+kind:''));const heading=el('div','section-label');heading.append(el('h3','',title));
    if(text){const copy=el('button','copy','复制');copy.type='button';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(text);copy.textContent='已复制';setTimeout(()=>copy.textContent='复制',1200);}catch{copy.textContent='复制失败';}});heading.append(copy);}
    box.append(heading,el('pre','',text||'（暂无记录）'));return box;
  }
  function renderDetail(){
    const root=$('detail');root.replaceChildren();const row=current();if(!row){const empty=el('div','empty');empty.append(el('span','empty-mark','↔'),el('h2','',state.snapshot?'暂无可显示的任务':'等待交互记录'),el('p','',state.snapshot?'当 OII 创建真实 DSH 请求后，输入、执行结果和投递状态会显示在这里。':'连接后可以查看每次请求是否获批、是否启动 DSH、它返回了什么，以及结果消息是否送达 QQ。'));if(!state.snapshot){const b=el('button','button primary','连接观察台');b.type='button';b.addEventListener('click',showAuth);empty.append(b);}root.append(empty);return;}
    state.selected=row.id;
    const head=el('div','detail-head'),left=el('div');left.append(el('h2','',row.operation==='install_runtime'?'安装 DSH 运行时':String(row.task||'DSH 任务').replace(/\s+/g,' ').slice(0,120)));
    const meta=el('div','detail-meta');meta.append(el('span','',`请求 ${row.id.slice(0,12)}`),el('span','',`创建 ${time(row.created)}`));if(row.approved)meta.append(el('span','',`确认 ${time(row.approved)}`));if(row.finished)meta.append(el('span','',`结束 ${time(row.finished)}`));left.append(meta);head.append(left,el('span','state '+stateClass(row.status),statusName[row.status]||row.status));root.append(head);
    root.append(panel('任务说明',row.task||'（任务内容未保存）'));
    const timeline=el('ol','timeline');for(const item of createTimeline(row)){
      const li=el('li');if(/failed|interrupted|rejected|expired/.test(item.stage))li.classList.add('failure');else if(/succeeded|delivered|approved|spawned/.test(item.stage))li.classList.add('success');else if(/superseded|unavailable/.test(item.stage))li.classList.add('muted');
      li.append(el('time','',time(item.created).slice(-8)),el('span','marker',''),el('strong','',stageName[item.stage]||item.stage),el('span','event-detail',item.detail||''));timeline.append(li);
    }
    const timelineWrap=el('section','');const timelineHeading=el('div','section-label');timelineHeading.append(el('h3','', '交互时间线'));timelineWrap.append(timelineHeading,timeline);root.append(timelineWrap);
    const actualInput=row.input_text||((row.status==='pending'||row.status==='rejected'||row.status==='expired'||row.status==='superseded')?'尚未启动 DSH，因此没有向 DSH 发送任务输入。':'该任务在交互追踪上线前提交；未保留完整启动输入。');
    const outputs=el('div','outputs');outputs.append(panel('OII → DSH · 实际传入内容',actualInput));
    outputs.append(panel('DSH 标准输出',row.stdout||'（DSH 尚无标准输出）'));
    outputs.append(panel('DSH 错误输出',row.stderr||((row.status==='failed'||row.status==='interrupted')?row.result:'（DSH 尚无错误输出）'),'error'));
    if(row.result)outputs.append(panel('OII 收到的最终执行结果',row.result,'status'));
    root.append(outputs);
    const delivery=el('section','delivery'),deliveryStatus=row.notification_status||'not_sent';delivery.append(el('h3','', 'DSH 结果 → QQ 投递'));
    let deliveryText=deliveryStatus==='delivered'?`已送达 · ${time(row.notification_at)}`:deliveryStatus==='failed'?`投递失败 · ${time(row.notification_at)}${row.notification_error?' · '+row.notification_error:''}`:deliveryStatus==='unavailable'?'发送器不可用，未能发送。':row.status==='pending'||row.status==='running'?'任务尚未完成，暂未投递结果。':'尚无投递回执记录。';delivery.append(el('p','',deliveryText));root.append(delivery);
  }
  function renderHealth(){
    const data=state.snapshot;if(!data)return;
    setHealth('dsh-runtime',data.enabled?(data.runtime_available?'已启用 · CLI 可定位':'已启用 · 未找到 CLI'):'已关闭',data.enabled&&data.runtime_available?'good':'bad');
    const files=data.reserved_env_files||[];
    const startupConfigured=data.base_url_inherited&&data.api_key_inherited;
    setHealth('env-state',files.length?'检测到受保护配置':startupConfigured?'Base URL 与 API Key 已传入':data.base_url_inherited||data.api_key_inherited?'启动环境配置不完整':'未检测到启动阻塞',files.length?'bad':startupConfigured?'good':data.base_url_inherited||data.api_key_inherited?'warn':'neutral');
    setHealth('record-count',`${data.requests?.length||0} 条`,(data.requests||[]).length?'good':'neutral');
    $('updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const warning=$('env-warning');warning.replaceChildren();
    if(files.length){warning.hidden=false;warning.append(el('strong','', 'DSH 启动配置会阻止任务运行'));
      warning.append(el('span','', '发现 DEEPSEEK_BASE_URL 写在 .env 文件中。DSH 只接受由启动进程传入该变量；请将 DEEPSEEK_BASE_URL 和 OII 使用的 DEEPSEEK_API_KEY 一起移入 OII 项目根目录的 .dsh-launch.env，并从 .env 删除这两行，然后重新安装/重载 systemd 服务。'));
      const list=el('ul');for(const file of files)list.append(el('li','',file));warning.append(list,el('p','', '服务端操作完成后运行：sudo bash auto-start.sh install'));}
    else warning.hidden=true;
  }
  async function load(){
    if(!state.key||state.busy)return;state.busy=true;
    try{const response=await fetch('/api/dsh/requests?limit=50',{headers:{Authorization:'Bearer '+state.key},cache:'no-store'});
      if(response.status===401){state.snapshot=null;$('auth-error').textContent='访问密钥不正确或已过期。';notice('观察密钥验证失败。');showAuth();return;}
      if(!response.ok)throw new Error(response.status===503?'DSH 观察服务不可用；确认学习/DSH 接入已启用。':'读取 DSH 交互记录失败。');
      state.snapshot=await response.json();notice('');renderHealth();renderRequests();renderDetail();
    }catch(error){notice(error.message||'读取 DSH 交互记录失败。');}
    finally{state.busy=false;}
  }
  $('connect').addEventListener('click',showAuth);$('connect-empty').addEventListener('click',showAuth);$('close-dialog').addEventListener('click',()=>$('auth-dialog').close());
  $('auth-form').addEventListener('submit',async event=>{event.preventDefault();state.key=$('token').value.trim();$('auth-error').textContent='';await load();if(state.snapshot){try{sessionStorage.setItem(storageKey,state.key);}catch{}$('auth-dialog').close();}});
  $('disconnect').addEventListener('click',()=>{state.key='';state.snapshot=null;state.selected='';try{sessionStorage.removeItem(storageKey);}catch{}$('auth-dialog').close();notice('');renderRequests();renderDetail();setHealth('dsh-runtime','尚未连接');setHealth('env-state','尚未连接');setHealth('record-count','尚未连接');});
  $('refresh').addEventListener('click',load);$('status-filter').addEventListener('change',event=>{state.filter=event.target.value;renderRequests();if(!visible().some(row=>row.id===state.selected))state.selected='';renderDetail();});
  $('auto-refresh').addEventListener('change',event=>{try{sessionStorage.setItem('dsh.monitor.auto',event.target.checked?'1':'0');}catch{}});
  try{$('auto-refresh').checked=sessionStorage.getItem('dsh.monitor.auto')!=='0';}catch{}
  if(state.key)load();else{renderRequests();renderDetail();}
  setInterval(()=>{if(state.key&&!state.busy&&$('auto-refresh').checked)load();},15000);
})();
