// Native Node bridge: no DSH runtime, SnowLuma, browser or npm install needed.
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SurfBili, credentials, validateBvid } from './surfcore.mjs';

export async function storyboard(input,dir,options={}) {
  const {runStoryboard}=await import('./vendor/storyboard/src/pipeline.js');
  const result=await runStoryboard({input,from:options.from||0,to:options.to,
    cells:options.cells===16?16:9,sheets:1,frameConcurrency:2,refineBursts:false,
    adaptive:false,pick:'mid',burn:'index',transcript:options.transcriptFile?'auto':'off',
    transcriptFile:options.transcriptFile,asrKey:null,audioStrip:false,ocr:false,budget:640000});
  const sheet=result.sheets[0];
  if (!sheet?.png) throw Error('storyboard_empty');
  await writeFile(join(dir,'sheet.png'),sheet.png,{mode:0o600});
  return {window:result.window,cells:sheet.cells,width:sheet.width,height:sheet.height,
    coverage:'sampled_frames_only',transcript:result.transcript,producer:'dsh-video-storyboard'};
}
async function main() {
  let raw='';for await(const chunk of process.stdin) {raw+=chunk;if(raw.length>16000) throw Error('input_limit');}
  const opts=JSON.parse(raw);
  const api=new SurfBili(await credentials());
  if (opts.operation==='search') return api.search(String(opts.query||'').slice(0,80));
  const bvid=validateBvid(opts.video);
  const view=await api.view(bvid);
  const metadata={bvid,title:String(view.title||'').slice(0,300),duration:view.duration,author:view.owner?.name,url:'https://www.bilibili.com/video/'+bvid};
  if(opts.operation==='info') return metadata;
  const sub=await api.subtitles(bvid,view).catch(()=>({segments:[],source:'unavailable'}));
  if(opts.operation==='subtitles') return {...metadata,...sub,segments:sub.segments.slice(0,300)};
  if(opts.operation!=='storyboard') throw Error('unsupported_operation');
  if (!(view.duration>0 && view.duration<=1800)) throw Error('video_duration_limit');
  const from=Number(opts.from||0),to=Math.min(Number(opts.to||view.duration),view.duration);
  if(!Number.isFinite(from)||!Number.isFinite(to)||from<0||to<=from||to-from>600) throw Error('video_window_limit');
  const dir=resolve(opts.directory);await mkdir(dir,{recursive:true,mode:0o700});
  const file=join(dir,'source.mp4'),transcriptFile=join(dir,'subtitles.json');
  try {
    const download=await api.download(bvid,view,file,160*1024*1024);
    if(sub.segments.length) await writeFile(transcriptFile,JSON.stringify(sub.segments),{mode:0o600});
    const result=await storyboard(file,dir,{from,to,cells:opts.cells,transcriptFile:sub.segments.length?transcriptFile:undefined});
    return {...metadata,...result,download,subtitle_source:sub.source};
  } finally { await rm(file,{force:true});await rm(transcriptFile,{force:true}); }
}
if (process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)) main().then(result=>process.stdout.write(JSON.stringify(result)+'\n')).catch(error=>{
  // Neither network URLs (signed tokens), Cookie values nor file paths are logged.
  const known=new Set(['bilibili_login_required','bilibili_credentials_invalid','bilibili_stream_unsupported',
    'bilibili_size_limit','bilibili_http_error','bilibili_api_error','bilibili_request_timeout',
    'video_duration_limit','video_window_limit','bilibili_wbi_unavailable']);
  const code=known.has(error.message)?error.message:error.code==='ENOENT'?'video_dependency_missing':'video_operation_failed';
  process.stdout.write(JSON.stringify({error:code,hint:'Check login, ffmpeg/ffprobe and duration/stream limits.'})+'\n');process.exitCode=1;
});
