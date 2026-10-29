// Adapted from Mieksido/surfcore commands/search.mjs, subtitles.mjs and
// bili-watch.mjs at d7f6b33d57b1667508c48206cbcc455815d6d336 (MIT).
// Copyright (c) 2026 Derpyu520 and qq-bridge / QSH contributors.
// OII adaptation: JSON API, bounded downloads, Linux support, shared login.
import { readFile, open } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import https from 'node:https';

const UA = 'Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36';
const MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52];
export function validateBvid(value) {
  if (!/^BV[0-9A-Za-z]{10}$/.test(value)) throw Error('invalid_bvid');
  return value;
}
export function publicV4(ip) {
  const a = ip.split('.').map(Number);
  return a.length === 4 && a.every(x => Number.isInteger(x) && x >= 0 && x <= 255)
    && ![0,10,127].includes(a[0]) && a[0] < 224
    && !(a[0] === 169 && a[1] === 254) && !(a[0] === 172 && a[1] >= 16 && a[1] <= 31)
    && !(a[0] === 192 && a[1] === 168) && !(a[0] === 100 && a[1] >= 64 && a[1] <= 127);
}
export function allowedUrl(raw) {
  const u = new URL(raw.startsWith('//') ? 'https:' + raw : raw);
  const domains = ['bilivideo.com','bilivideo.cn','hdslb.com'];
  if (u.protocol !== 'https:' || u.username || u.password || (u.port && u.port !== '443')
    || !(u.hostname === 'api.bilibili.com' || domains.some(d => u.hostname === d || u.hostname.endsWith('.'+d)))) throw Error('bilibili_host_rejected');
  return u;
}
export async function credentials() {
  // Explicit SurfCore account wins; otherwise use the existing Bilibili MCP
  // account. Never silently read a different account from ~/.dsh.
  if (process.env.OII_SURFCORE_CREDENTIALS) {
    return JSON.parse(await readFile(process.env.OII_SURFCORE_CREDENTIALS, 'utf8'));
  }
  if (process.env.BILIBILI_SESSDATA) return {
    sessdata:process.env.BILIBILI_SESSDATA,bili_jct:process.env.BILIBILI_BILI_JCT,
    dedeuserid:process.env.BILIBILI_DEDEUSERID,
  };
  try { return JSON.parse(await readFile(join(homedir(),'.bilibili-mcp','config.json'),'utf8')); }
  catch { throw Error('bilibili_login_required'); }
}
export async function request(raw, cookie, maxBytes=4*1024*1024, redirects=0, consume=null) {
  const u=allowedUrl(raw);
  const addresses=await lookup(u.hostname,{family:4,all:true});
  if (!addresses.length || addresses.some(a=>!publicV4(a.address))) throw Error('bilibili_address_rejected');
  return new Promise((resolve,reject)=>{
    const req=https.get(u, {headers:{'user-agent':UA,referer:'https://www.bilibili.com/',
      ...(cookie && u.hostname==='api.bilibili.com' ? {cookie} : {})},
      lookup:(_h,options,cb)=> options?.all ? cb(null,[addresses[0]]) : cb(null,addresses[0].address,4)}, res=>{
      (async()=>{
        if ([301,302,303,307,308].includes(res.statusCode)) {
          res.resume();
          if (redirects>=3) throw Error('bilibili_redirect_limit');
          return request(new URL(res.headers.location,u).href,cookie,maxBytes,redirects+1,consume);
        }
        if (res.statusCode!==200) { res.resume(); throw Error('bilibili_http_error'); }
        if (Number(res.headers['content-length']||0)>maxBytes) { res.destroy(); throw Error('bilibili_size_limit'); }
        let bytes=0;const chunks=[];
        for await (const chunk of res) {
          bytes+=chunk.length;
          if (bytes>maxBytes) { res.destroy();throw Error('bilibili_size_limit'); }
          if (consume) await consume(chunk); else chunks.push(chunk);
        }
        return consume ? bytes : Buffer.concat(chunks);
      })().then(resolve,reject);
    });
    const timer=setTimeout(()=>req.destroy(Error('bilibili_request_timeout')),120000);
    req.once('close',()=>clearTimeout(timer));req.once('error',reject);
  });
}
export function sign(params, raw) {
  const mixin=MIXIN.map(i=>raw[i]).join('').slice(0,32);
  if (mixin.length!==32) throw Error('bilibili_wbi_unavailable');
  const p={...params,wts:Math.round(Date.now()/1000)};
  const q=Object.keys(p).sort().map(k=>`${encodeURIComponent(k)}=${encodeURIComponent(String(p[k]).replace(/[!'()*]/g,''))}`).join('&');
  return q+'&w_rid='+createHash('md5').update(q+mixin).digest('hex');
}
export class SurfBili {
  constructor(cred) {
    if (!cred.sessdata) throw Error('bilibili_login_required');
    for (const key of ['sessdata','bili_jct','buvid3','dedeuserid']) {
      if (/[\r\n;]/.test(String(cred[key]||''))) throw Error('bilibili_credentials_invalid');
    }
    this.cookie=[['SESSDATA',cred.sessdata],['bili_jct',cred.bili_jct],['buvid3',cred.buvid3],['DedeUserID',cred.dedeuserid||cred.mid]].filter(x=>x[1]).map(x=>x.join('=')).join('; ');
  }
  async get(endpoint) {
    const j=JSON.parse(await request('https://api.bilibili.com'+endpoint,this.cookie));
    if (j.code!==0) throw Error(j.code===-101 ? 'bilibili_login_required' : 'bilibili_api_error');
    return j.data;
  }
  async signed(endpoint,params) {
    const nav=await this.get('/x/web-interface/nav');
    const key=u=>String(u||'').split('/').pop().split('.')[0];
    return this.get(endpoint+'?'+sign(params,key(nav.wbi_img?.img_url)+key(nav.wbi_img?.sub_url)));
  }
  async view(bvid) { return this.get('/x/web-interface/view?bvid='+validateBvid(bvid)); }
  async search(query) {
    const data=await this.signed('/x/web-interface/wbi/search/type',{search_type:'video',keyword:query,page:1});
    return {videos:(data.result||[]).slice(0,5).map(x=>({bvid:x.bvid,title:String(x.title||'').replace(/<[^>]*>/g,''),author:x.author,duration:x.duration}))};
  }
  async subtitles(bvid,view) {
    const data=await this.signed('/x/player/wbi/v2',{bvid:validateBvid(bvid),cid:view.cid});
    const list=data.subtitle?.subtitles||[];
    const sub=list.find(x=>x.lan==='zh-CN')||list.find(x=>x.lan==='ai-zh')||list[0];
    if (!sub?.subtitle_url) return {segments:[],source:'unavailable'};
    const json=JSON.parse(await request(sub.subtitle_url,null));
    return {source:sub.lan,segments:(json.body||[]).slice(0,12000).map(x=>({from:x.from,to:x.to,text:String(x.content||'').slice(0,2000)}))};
  }
  async download(bvid,view,file,maxBytes) {
    const data=await this.get(`/x/player/playurl?bvid=${validateBvid(bvid)}&cid=${view.cid}&qn=32&fnval=1&platform=pc`);
    if (data.durl?.length!==1 || !data.durl[0].url) throw Error('bilibili_stream_unsupported');
    const media=new URL(data.durl[0].url);
    if (media.protocol==='http:') media.protocol='https:';
    const handle=await open(file,'w',0o600);
    try {
      const bytes=await request(media.href,null,maxBytes,0,chunk=>handle.writeFile(chunk));
      return {bytes,quality:data.quality};
    } finally { await handle.close(); }
  }
}
