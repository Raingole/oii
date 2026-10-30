// Native Node bridge: no DSH runtime, SnowLuma, browser or npm install needed.
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { SurfBili, credentials, validateBvid } from './surfcore.mjs';

const execFileAsync=promisify(execFile);

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
export async function captureVideoFrame(input,at,output) {
  if(!Number.isFinite(at)||at<0) throw Error('video_screenshot_time_invalid');
  const ffmpeg=process.env.DSH_FFMPEG_PATH||process.env.FFMPEG_PATH||'ffmpeg';
  await execFileAsync(ffmpeg,['-hide_banner','-loglevel','error','-ss',String(at),'-i',input,
    '-frames:v','1','-vf','scale=1280:-2:force_original_aspect_ratio=decrease','-y',output],
    {timeout:45000,maxBuffer:1024*1024});
  const png=await readFile(output);
  if(png.length<128||png.length>5*1024*1024||png.subarray(1,4).toString()!=='PNG') throw Error('video_screenshot_invalid');
  return {bytes:png.length,at};
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
  if(opts.operation==='screenshot') {
    const at=Number(opts.at_seconds);
    if(!Number.isFinite(at)||at<0||at>=view.duration) throw Error('video_screenshot_time_invalid');
    const dir=resolve(opts.directory);await mkdir(dir,{recursive:true,mode:0o700});
    const file=join(dir,'source.mp4'),sheet=join(dir,'sheet.png');
    try {
      const download=await api.download(bvid,view,file,160*1024*1024);
      const frame=await captureVideoFrame(file,at,sheet);
      return {...metadata,window:{from:at,to:at+1},cells:[{i:1,t_start:at,t_end:at+1,t_frame:at}],
        coverage:'single_video_frame',download,producer:'ffmpeg-linux-headless',frame_bytes:frame.bytes};
    } finally {await rm(file,{force:true});}
  }
  if(opts.operation==='audio_clip') {
    const from=Number(opts.from),to=Number(opts.to);
    if(!Number.isFinite(from)||!Number.isFinite(to)||from<0||to<=from||to-from>12||to>view.duration) throw Error('video_window_limit');
    const dir=resolve(opts.directory);await mkdir(dir,{recursive:true,mode:0o700});
    const file=join(dir,'source.mp4'),clip=join(dir,'clip.mp3');
    try {
      const download=await api.download(bvid,view,file,160*1024*1024);
      const ffmpeg=process.env.DSH_FFMPEG_PATH||process.env.FFMPEG_PATH||'ffmpeg';
      await execFileAsync(ffmpeg,['-hide_banner','-loglevel','error','-nostdin','-ss',String(from),'-i',file,
        '-t',String(to-from),'-vn','-ac','1','-ar','24000','-b:a','48k','-y',clip],
        {timeout:60000,maxBuffer:1024*1024});
      const audio=await readFile(clip);
      if(audio.length<128||audio.length>2*1024*1024) throw Error('bilibili_size_limit');
      return {...metadata,from,to,download,audio_bytes:audio.length};
    } finally {await rm(file,{force:true});}
  }
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
    'video_duration_limit','video_window_limit','video_screenshot_time_invalid','video_screenshot_invalid',
    'bilibili_wbi_unavailable']);
  const code=known.has(error.message)?error.message:error.code==='ENOENT'?'video_dependency_missing':'video_operation_failed';
  process.stdout.write(JSON.stringify({error:code,hint:'Check login, ffmpeg/ffprobe and duration/stream limits.'})+'\n');process.exitCode=1;
});
