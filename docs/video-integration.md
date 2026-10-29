# B站分镜与QQ发图

中控已接入 SurfCore 的 B 站只读 API 逻辑和 dsh-video-storyboard 的独立分镜管线。保留原思维核心、MMTR 和知识库；无需另起完整 DSH。SurfCore 原生整套按 Windows/SnowLuma 工作，本次接入的是适合 Linux 服务端的独立部分。

## 云端更新

```bash
cd /home/oii
sudo apt-get update
sudo apt-get install -y ffmpeg
sudo bash auto-update.sh run
./venv/bin/python -m core.knowledge.cli status
```

尚未登录B站时，用运行服务的同一系统用户执行：

```bash
./venv/bin/python -m core.knowledge.cli login
```

默认复用 `~/.bilibili-mcp/config.json` 或现有 BILIBILI_* 环境变量。如果要指定另一个 SurfCore 账号，在私有 `data/.config.yaml` 设置 `learning.video.surfcore_credentials` 为其 credentials.json 路径；不自动混用 `~/.dsh` 的账号，不把 Cookie 发给模型。

## QQ里怎么用

- “看看 BVxxxxxxxxxx 的前五分钟，把分镜图发给我。”
- “细看这个视频 120 到 180 秒，做16格。”
- “截一下 BV 视频 123 秒的画面发给我。”
- “查一下刚才那个视频任务的结果。”
- “把这张公开图片链接发给我：https://……”

模型通过工具调用选择动作，不能仅凭口头答应当作已执行。`bilibili_watch_video` 立即返回 job_id，单独的后台队列处理；成功后尝试给发起任务的当前 NapCat 私聊发图。`video_job_status` 查询实际结果、图片编号及投递状态。生成图可用 `qq_send_image` 再发，公开HTTPS图片也可发送。每次发送依赖 OneBot 回执，超时不自动重复发送。

`surfcore_video_info`、`surfcore_video_subtitles` 读取元信息和字幕；字幕上限300条。原 `bilibili_search`、`bilibili_learn_video`、`knowledge_search` 和基于聊天兴趣的后台学习继续可用。

## 自主兴趣分享

中控会从当前 QQ 私聊里用户明确表达的兴趣词开始查找 B 站视频。默认每隔随机3到6小时检查一次，每天最多自动分享1条、搜索尝试最多3次；只在最近7天有私聊、且对话空闲至少30分钟时分享，并遵守默认凌晨0点至8点的安静时间。挑选搜索结果时跳过超过30分钟和本会话知识库里已有的视频；成功入队后通过前述分镜流程检查画面，给最近私聊发视频链接和抽样图。搜索失败、视频格式不支持、字幕／网络受限都会跳过该条，不会编造已看过的结论。使用 `bilibili_autoshare` 工具可以查看或切换开关，也可以直接在 QQ 说“停止自主分享视频”。

自主推荐属于只读浏览与发给用户的私聊分享，不会点赞、评论、关注、收藏或发公开动态。登录、单 MP4 流和 FFmpeg 限制与手动分镜相同。收到足够明确的兴趣记录后才开始排程；刚更新后会先等待随机间隔再分享，不会立刻刷屏。

## 当前范围

分镜默认9格、单张64万像素，允许16格；一次最多看10分钟，视频总长最多30分钟，源文件最多160MiB，同时只处理一个任务，最多排队8个。`bilibili_video_screenshot` 可从指定时间点抽一张 PNG；抽帧直接走 FFmpeg，无需 Chromium、X11 或桌面环境。生成后删除源视频。需要细看时指定更窄时间段；只把编号、时间、少量字幕和可用的视觉笔记交给文本模型，不塞整段视频或图片base64进文本上下文。

图片理解复用已配置的 VLLM；未配置时仍能生成和发送分镜图，但结果明确没有视觉理解笔记。可用字幕或视觉笔记进入当前会话的知识库，资料仍是外部来源。已存在同一视频来源的笔记按知识库原有规则去重。

当前支持单文件 MP4 播放流。仅DASH、多段流、登录失效、风控或网络限制会显示失败，不假报“看完”。当前图片发送支持 NapCat 私聊；QQ官方接口和群聊尚未接入该发图工具。B站点赞、评论、动态和私信未在本次只读适配里开放。

图片和任务保存在忽略Git的 `data/video` 和知识数据库中。每张图片绑定会话，工具不能选其他QQ收件人、读取任意服务器文件。运维按需清理历史图片；当前没有自动清理策略。语音合成仍待定。
