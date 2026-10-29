(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const keyName='mmtr.observer.key.v1';
  const state={key:'',data:null,filter:'all',query:'',busy:false,images:[]};
  try{state.key=sessionStorage.getItem(keyName)||'';}catch{}
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const when=value=>{const date=new Date(Number(value)*1000);return Number.isFinite(+date)?new Intl.DateTimeFormat('zh-CN',{month:'short',day:'numeric'}).format(date):'时间未记录';};
  const fields=text=>{const result={};for(const line of String(text||'').split('\n')){const match=line.match(/^([^：:]{1,20})[：:]\s*(.*)$/);if(match)result[match[1]]=match[2];}return result;};
  function notice(message){const box=$('notice');box.textContent=message;box.hidden=!message;}
  function setStatus(message,online=false){$('status').textContent=message;$('status-dot').classList.toggle('online',online);}
  function sourceLink(url,label='打开原视频 ↗'){
    if(!/^https:\/\/www\.bilibili\.com\/video\/BV[0-9A-Za-z]{10}/.test(String(url||'')))return null;
    const link=el('a','card-link',label);link.href=url;link.target='_blank';link.rel='noopener noreferrer';return link;
  }
  function rowLabel(parent,label,value){if(!value)return;const p=el('p');p.append(el('span','label',label),document.createTextNode(String(value)));parent.append(p);}
  function memeCard(source,chunk){
    const parsed=fields(chunk.text),card=el('article','card'),body=el('div','card-body'),top=el('div','card-top');
    top.append(el('span','badge','B站梗'),el('span','date',when(source.created)));body.append(top);
    body.append(el('h3','',parsed['梗词']||'梗条目'));
    rowLabel(body,'含义',parsed['含义']);rowLabel(body,'语境',parsed['使用语境']);rowLabel(body,'来源说法',parsed['来源']);
    if(parsed['字幕证据'])body.append(el('div','quote','“'+parsed['字幕证据']+'”'));
    const link=sourceLink(source.url+(chunk.start!==null&&chunk.start!==undefined?((source.url.includes('?')?'&':'?')+'t='+Math.floor(chunk.start)):''),'查看来源与时间点 ↗');if(link)body.append(link);
    card.append(body);return card;
  }
  function videoCard(source){
    const card=el('article','card'),body=el('div','card-body'),top=el('div','card-top');
    top.append(el('span','badge video','视频笔记'),el('span','date',when(source.created)));body.append(top);
    body.append(el('h3','',source.title||'B站学习笔记'));
    if(source.summary)body.append(el('p','',source.summary));
    for(const chunk of (source.chunks||[]).slice(0,2)){
      if(chunk.text)body.append(el('div','quote',String(chunk.text).slice(0,240)));
      if(chunk.start!==null&&chunk.start!==undefined)body.append(el('span','date','字幕时间 '+Math.floor(chunk.start/60)+':'+String(Math.floor(chunk.start%60)).padStart(2,'0')));
    }
    const link=sourceLink(source.url);if(link)body.append(link);card.append(body);return card;
  }
  function imageCard(item){
    const card=el('article','card'),cover=el('div','image-cover'),body=el('div','card-body'),top=el('div','card-top');
    const placeholder=el('span','image-placeholder','▧');cover.append(placeholder);
    const image=document.createElement('img');image.alt='已保存的 QQ 图片或表情包';image.loading='lazy';image.hidden=true;
    if(/^[0-9a-f]{64}$/.test(item.hash)){
      fetch('/api/knowledge/images/'+item.hash,{headers:{Authorization:'Bearer '+state.key},cache:'no-store'})
        .then(response=>{if(!response.ok)throw new Error('image_unavailable');return response.blob();})
        .then(blob=>{const objectUrl=URL.createObjectURL(blob);state.images.push(objectUrl);image.src=objectUrl;image.hidden=false;placeholder.remove();})
        .catch(()=>{});
    }
    cover.append(image);top.append(el('span','badge image',item.payload?.kind==='meme'?'表情包':'图片'),el('span','date',when(item.created)));body.append(top);
    const payload=item.payload||{};body.append(el('h3','',payload.description||payload.visible_text||'已保存图片'));
    rowLabel(body,'图中文字',payload.visible_text);rowLabel(body,'意图',payload.intent);rowLabel(body,'适用语境',payload.context);
    for(const emotion of (payload.expressed_emotions||[]).slice(0,3))body.append(el('span','emotion',emotion.label+(emotion.evidence?' · '+emotion.evidence:'')));
    card.append(cover,body);return card;
  }
  function refreshCounts(data){const counts=data.counts||{};$('count-memes').textContent=counts.meme_entries??0;$('count-videos').textContent=counts.video_notes??0;$('count-images').textContent=counts.images??0;$('count-sources').textContent=counts.sources??0;$('filter-meme').textContent=counts.meme_entries??0;$('filter-video').textContent=counts.video_notes??0;$('filter-image').textContent=counts.images??0;$('filter-all').textContent=(counts.meme_entries||0)+(counts.video_notes||0)+(counts.images||0);}
  function visibleCards(){
    const data=state.data;if(!data)return[];const query=state.query.trim().toLocaleLowerCase();const cards=[];
    for(const source of data.sources||[]){
      const memes=source.data_source==='bilibili_meme_transcript';
      if(state.filter==='meme'&&!memes)continue;if(state.filter==='video'&&memes)continue;if(state.filter==='image')continue;
      const chunks=source.chunks||[];
      if(memes){for(const chunk of chunks){const blob=[source.title,source.summary,chunk.text].join(' ').toLocaleLowerCase();if(!query||blob.includes(query))cards.push(memeCard(source,chunk));}}
      else {const blob=[source.title,source.summary,...chunks.map(c=>c.text)].join(' ').toLocaleLowerCase();if(!query||blob.includes(query))cards.push(videoCard(source));}
    }
    if(state.filter==='all'||state.filter==='image')for(const image of data.images||[]){const blob=JSON.stringify(image.payload||{}).toLocaleLowerCase();if(!query||blob.includes(query))cards.push(imageCard(image));}
    return cards;
  }
  function render(){
    for(const url of state.images)URL.revokeObjectURL(url);state.images=[];
    const container=$('cards');container.replaceChildren();const cards=visibleCards();
    if(!cards.length){const empty=el('div','empty');empty.append(el('span','','✳'),el('h3','',state.data?'没有匹配内容':'知识库还没连接'),el('p','',state.data?'换个词试试，或者切换知识类型。':'连接后查看已收录的梗、视频笔记和图片。'));if(!state.data){const button=el('button','button dark','连接知识库');button.type='button';button.addEventListener('click',openDialog);empty.append(button);}container.append(empty);return;}
    for(const card of cards)container.append(card);
  }
  async function load(){
    if(!state.key)return;state.busy=true;setStatus('正在读取');
    try{const response=await fetch('/api/knowledge/library',{headers:{Authorization:'Bearer '+state.key},cache:'no-store'});if(response.status===401){state.data=null;setStatus('密钥无效');$('auth-error').textContent='访问密钥不正确或已过期。';openDialog();return;}if(!response.ok)throw new Error('知识库服务暂不可用');
      state.data=await response.json();refreshCounts(state.data);render();setStatus('已连接',true);notice('');$('updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
    }catch(error){setStatus('连接失败');notice(error.message||'读取知识库失败。');}finally{state.busy=false;}
  }
  function openDialog(){if(!$('auth-dialog').open)$('auth-dialog').showModal();}
  $('connect').addEventListener('click',openDialog);$('connect-empty').addEventListener('click',openDialog);$('close-dialog').addEventListener('click',()=>$('auth-dialog').close());
  $('auth-form').addEventListener('submit',async event=>{event.preventDefault();state.key=$('token').value.trim();$('auth-error').textContent='';await load();if(state.data){try{sessionStorage.setItem(keyName,state.key);}catch{}$('auth-dialog').close();}});
  $('disconnect').addEventListener('click',()=>{state.key='';state.data=null;try{sessionStorage.removeItem(keyName);}catch{}$('auth-dialog').close();refreshCounts({counts:{}});render();setStatus('已断开');});
  $('refresh').addEventListener('click',load);$('search').addEventListener('input',event=>{state.query=event.target.value;render();});
  document.querySelectorAll('.filter').forEach(button=>button.addEventListener('click',()=>{state.filter=button.dataset.filter;document.querySelectorAll('.filter').forEach(item=>item.classList.toggle('active',item===button));render();}));
  if(state.key)load();else render();setInterval(()=>{if(state.key&&!state.busy)load();},60000);
})();
