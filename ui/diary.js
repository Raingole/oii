(() => {
  'use strict';
  const keyName='mmtr.observer.key.v1', state={key:'',busy:false};
  const $=id=>document.getElementById(id);
  const validKey=value=>/^[\x21-\x7e]+$/.test(value);
  try{state.key=sessionStorage.getItem(keyName)||'';}catch{}
  function showAuth(){if(!$('auth').open)$('auth').showModal();}
  function render(entries){const list=$('list');list.replaceChildren();if(!entries.length){const empty=document.createElement('article');empty.className='empty';empty.textContent='还没有日记。它会在当天晚些时候，写下自己觉得值得留下的片段。';list.append(empty);return;}
    for(const item of entries){const article=document.createElement('article');article.className='entry';const date=document.createElement('time');date.dateTime=item.day;date.textContent=new Intl.DateTimeFormat('zh-CN',{dateStyle:'full'}).format(new Date(item.day+'T12:00:00'));const text=document.createElement('p');text.textContent=item.content;article.append(date,text);list.append(article);}}
  async function load(){if(!state.key)return false;if(!validKey(state.key)){$('status').textContent='访问密钥格式不正确';$('error').textContent='请只粘贴命令输出的访问密钥，不要包含说明文字或换行。';try{sessionStorage.removeItem(keyName);}catch{}state.key='';showAuth();return false;}state.busy=true;$('status').textContent='正在打开日记';try{const response=await fetch('/api/private/diary',{headers:{Authorization:'Bearer '+state.key},cache:'no-store'});if(response.status===401){$('status').textContent='访问密钥无效';$('error').textContent='密钥不正确或已过期。';showAuth();return false;}if(!response.ok)throw new Error('私人日记暂时无法读取');const data=await response.json();render(data.entries||[]);const messages={waiting:'日记已连接 · 今天的日记会在晚些时候写下',writing:'正在写今天的日记',failed:'今天的日记暂时没写成，会稍后重试',interrupted:'写作中断，会稍后重试',written:'日记已连接'};$('status').textContent=messages[data.status]||'日记已连接';return true;}catch(error){$('status').textContent=error.message||'连接失败';return false;}finally{state.busy=false;}}
  $('connect').addEventListener('click',showAuth);$('refresh').addEventListener('click',load);
  $('auth-form').addEventListener('submit',async event=>{event.preventDefault();state.key=$('token').value.trim();$('error').textContent='';if(!validKey(state.key)){$('error').textContent='请只粘贴命令输出的访问密钥，不要包含说明文字或非英文字符。';return;}if(await load()){try{sessionStorage.setItem(keyName,state.key);}catch{}$('auth').close();}});
  $('disconnect').addEventListener('click',()=>{state.key='';try{sessionStorage.removeItem(keyName);}catch{}render([]);$('status').textContent='已断开';$('auth').close();});
  if(state.key)load();else showAuth();setInterval(()=>{if(state.key&&!state.busy)load();},60000);
})();
