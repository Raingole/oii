"""Local operator commands; never print Cookie values."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[2]


def load():
    import yaml
    from config.config_loader import merge_configs
    base=yaml.safe_load((ROOT/'config.yaml').read_text(encoding='utf-8')) or {}
    path=ROOT/'data/.config.yaml'
    private=yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return merge_configs(base,private or {})


async def status(config):
    from .bilibili import BilibiliClient
    from .store import KnowledgeStore
    settings=config.get('learning',{}) or {}
    path=Path(settings.get('database','data/knowledge/knowledge.db'))
    store=KnowledgeStore(path if path.is_absolute() else ROOT/path)
    client=BilibiliClient(ROOT,settings.get('bilibili',{}))
    try:
        credentials=await client.call('check_bilibili_credentials')
        login={key:credentials.get(key) for key in ('configured','source','logged_in')}
    except Exception as exc:
        login={'status':'unavailable','error_type':type(exc).__name__}
        if str(exc)=='bilibili_runtime_missing':
            login.update(error='bilibili_runtime_missing',next_step='bash auto-start.sh prepare')
    finally:
        await client.close()
    selected=(config.get('selected_module') or {}).get('VLLM')
    vision=(config.get('VLLM') or {}).get(selected,{}) or {}
    key=str(vision.get('api_key',''))
    print(json.dumps({'bilibili':login,'knowledge':store.status(),'last_run':store.state('last_run'),
        'vision':{'selected':selected,'model':vision.get('model_name'),'key_configured':bool(key and '你的' not in key and 'YOUR_' not in key)},
        'video':{'enabled':(settings.get('video') or {}).get('enabled',True),
                 'ffmpeg':__import__('shutil').which((settings.get('video') or {}).get('ffmpeg') or 'ffmpeg') is not None,
                 'ffprobe':__import__('shutil').which((settings.get('video') or {}).get('ffprobe') or 'ffprobe') is not None,
                 'pipeline_present':(ROOT/'deploy/video/bridge.mjs').is_file()},
        'speech_synthesis':'pending'},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description='OII Bilibili learning and image understanding')
    parser.add_argument('action',choices=['status','login'])
    args=parser.parse_args()
    config=load()
    if args.action=='login':
        entry=ROOT/'deploy/bilibili/node_modules/@xzxzzx/bilibili-mcp/dist/cli.js'
        if not entry.is_file():
            raise SystemExit('先运行 bash auto-start.sh prepare 安装 B 站接入')
        node=os.environ.get('NODE_BIN') or (config.get('mmtr') or {}).get('node','node')
        result=subprocess.call([node,str(entry),'setup'],cwd=ROOT)
        if result==0:
            from .store import KnowledgeStore
            path=Path((config.get('learning') or {}).get('database','data/knowledge/knowledge.db'))
            store=KnowledgeStore(path if path.is_absolute() else ROOT/path)
            schedule=store.state('schedule');schedule['next_at']=0;store.state('schedule',schedule)
        raise SystemExit(result)
    asyncio.run(status(config))


if __name__=='__main__':
    main()
