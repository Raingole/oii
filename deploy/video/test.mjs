import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { allowedUrl,publicV4,validateBvid,sign } from './surfcore.mjs';
import { storyboard } from './bridge.mjs';

test('Bilibili adapter rejects local files, arbitrary hosts, credentials and private IPs',()=>{
  for(const value of ['file:///etc/passwd','https://127.0.0.1/','https://api.bilibili.com.evil.org/',
    'https://foo@api.bilibili.com/','https://api.bilibili.com:8443/']) assert.throws(()=>allowedUrl(value));
  assert.equal(allowedUrl('https://upos-sz.bilivideo.com/movie.mp4').protocol,'https:');
  for(const ip of ['127.0.0.1','10.0.0.1','172.16.0.1','169.254.169.254','100.64.0.1','::1']) assert.equal(publicV4(ip),false);
  assert.equal(publicV4('1.1.1.1'),true);
  assert.throws(()=>validateBvid('../../secret'));
  assert.match(sign({bvid:'BV1xx411c7mD'},'0123456789abcdef'.repeat(4)),/w_rid=[a-f0-9]{32}$/);
});

test('real ffmpeg storyboard produces a numbered PNG and aligned timestamped subtitles',{
  skip:!process.env.DSH_FFMPEG_PATH || !process.env.DSH_FFPROBE_PATH,timeout:90000,
},async()=>{
  const dir=await mkdtemp(join(tmpdir(),'oii-video-test-'));
  try {
    const input=join(dir,'fixture.mp4'),subs=join(dir,'subtitles.json');
    execFileSync(process.env.DSH_FFMPEG_PATH,['-y','-loglevel','error','-f','lavfi','-i',
      'testsrc2=size=320x180:rate=12','-t','15','-c:v','libx264','-pix_fmt','yuv420p',input]);
    await writeFile(subs,JSON.stringify([{from:0,to:7,text:'第一段字幕'},{from:7,to:15,text:'第二段字幕'}]));
    const result=await storyboard(input,dir,{from:0,to:15,cells:9,transcriptFile:subs});
    const png=await readFile(join(dir,'sheet.png'));
    assert.equal(png.subarray(1,4).toString(),'PNG');
    assert.ok(result.cells.length>=2 && result.cells.length<=9);
    assert.ok(result.cells.every(c=>c.i>=1 && c.t_start>=0 && c.t_end<=15));
    assert.ok(result.cells.some(c=>c.text?.includes('字幕')));
    assert.equal(result.coverage,'sampled_frames_only');
    assert.ok(result.width*result.height<=700000);
  } finally {await rm(dir,{recursive:true,force:true});}
});
