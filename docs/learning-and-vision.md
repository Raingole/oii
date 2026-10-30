# B 站自学、知识库与图片理解

## GitHub 项目选择

| 项目 | 能力与选择 |
| --- | --- |
| [XZXZZX-Ai/bilibili-mcp](https://github.com/XZXZZX-Ai/bilibili-mcp) | Apache-2.0；Node stdio MCP，可在 Linux 无桌面环境搜索并读取视频字幕。本项目固定实际已发布的 npm `@xzxzzx/bilibili-mcp@1.14.5`。调研时 GitHub 源码已标为 1.14.6，但 npm 尚未发布该版本，因此不直接跟随源码版本号。 |
| [QwenLM/Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | Apache-2.0；图片／视频理解模型。通过现有 OpenAI 兼容视觉接口接入，可使用自己部署的 Qwen3-VL 服务；也可以继续使用现有已配置的视觉提供商。未自动下载模型权重。 |
| [anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) | MIT；机器人表情包描述、分类和检索项目。用于调研与设计参考；它本身依赖 AstrBot。本项目实际接入的是 OII 自己的视觉消息管线，不声称已安装 AstrBot 插件。 |

B 站上游工具与模型的细节以各自说明为准。所有第三方网页、README、字幕和图中文字都作为外部数据处理。

## 抖音搜索、推荐流与知识学习

Linux 抖音接入使用 [pazwusimple-netizen/douyin-mcp](https://github.com/pazwusimple-netizen/douyin-mcp)，固定在 `deploy/douyin/UPSTREAM_REVISION.txt` 记录的提交，保留 MIT 许可。它提供关键词搜索、推荐流、作品详情、评论和 ASR 转写。OII 单独创建 `deploy/douyin/.venv` 安装依赖；依赖安装失败不会阻止中控启动。`data/douyin/cookies.txt`、临时下载和转写目录均被 Git 忽略。

OII 只开放登录状态、搜索、推荐流、作品详情、评论、转写和分享链接解析；上游的退出登录、下载等其他工具不对 Agent 开放。学习指定视频时会抓取字幕、最多20条评论，使用已有模型整理来源笔记并写入同一 SQLite 知识库；自动 ASR 默认复用 B 站安装的本地 faster-whisper CPU 模型，不需要额外 ASR API Key。调用 `douyin_homefeed` 只读取最多10条推荐条目，不会点赞、关注、评论或执行账号操作。

更新代码并安装隔离依赖后，在服务器终端以运行 OII 服务的同一 Linux 用户执行扫码登录。二维码会直接显示在终端：

```bash
cd /home/oii
sudo -u "$(stat -c %U .)" env DOUYIN_COOKIE_PATH=/home/oii/data/douyin/cookies.txt \
  /home/oii/deploy/douyin/.venv/bin/python \
  /home/oii/deploy/douyin/upstream/login.py --api
```

扫码成功后可在 QQ 私聊说“检查抖音登录状态”，再说“看看抖音知识类推荐”或“学习抖音上的……”。也可直接提供抖音视频链接。`douyin_learn_topic` 是立即执行的指定搜索，会先按标题和简介筛选相关视频；没有足够相关的结果或没有可用转写时会返回失败，不会把无关视频记成已学。抖音笔记会同时进入普通知识检索与日常语境召回。

如果本地 ASR 未自动找到模型，可在服务器 `.env` 中设置 `ASR_LOCAL_PYTHON` 和 `ASR_LOCAL_MODEL_DIR`，路径分别指向 B 站 ASR 虚拟环境与模型目录。默认使用本地 CPU ASR；如需切换云 ASR，可设置 `DOUYIN_ASR_PROVIDER` 和 upstream 对应的 API 环境变量；不要把密钥写入仓库或聊天。

Linux 上只有在抖音搜索触发浏览器回退时才需要 Chromium。为避免每次更新自动下载大型浏览器，首次需要回退时可用服务用户安装一次：

```bash
sudo -u "$(stat -c %U .)" /home/oii/deploy/douyin/.venv/bin/python -m playwright install chromium
```

常规 API 搜索和推荐流不依赖该浏览器。ASR 视频最长处理时长默认限制为 30 分钟，下载体积上限为 250 MB；任务总等待上限为 30 分钟，超时后中控回收 MCP 子进程，避免后台遗留进程。

依赖安装优先使用仓库锁定文件；服务器装有 `uv` 时严格按 `uv.lock` 同步，否则用 pip 安装。抖音安装失败会记录在部署日志，不会阻止其余 OII 服务启动。

该上游是非官方网页接口，使用其签名与浏览器指纹兼容代码，搜索遇到 verify_check 时还会重试并尝试 Playwright 回退；抖音可能变更接口或要求额外验证。OII 不会通过点赞、关注、评论等行为“训练”推荐算法。服务端的 Agent 接口仍限制为只读，并设定搜索/推荐条数上限。登录 Cookie 使用权限 0600 的本地文件，不进入 Git；上游签名文件中原有的采集样例请求已从内置副本删除。实际登录和接口可用性仍需在云端扫码后确认。

## 云端启用

```bash
cd /home/oii
sudo bash auto-update.sh run
sudo -u "$(stat -c %U .)" ./venv/bin/python -m core.knowledge.cli login
sudo -u "$(stat -c %U .)" ./venv/bin/python -m core.knowledge.cli status
```

如果服务器使用 `.venv`，将上面两条 Python 路径改为 `.venv/bin/python`。登录应使用运行 `oii.service` 的同一系统用户；当前项目服务按项目目录所有者运行。登录通过上游交互式 setup 完成，凭据保存在该用户的私有配置中。也可在服务器 `.env` 中配置 `BILIBILI_SESSDATA`、`BILIBILI_BILI_JCT`、`BILIBILI_DEDEUSERID`，更新环境后重启服务；不要将值提交 Git 或贴到聊天中。

更新器根据锁文件安装 npm 依赖，不执行安装脚本，输入不变时不重复安装。B 站进程由中控按需启动并在关闭时回收，随现有 systemd 服务管理，无需新开桌面浏览器或额外长期终端。

登录成功会唤醒待处理学习；首次心跳通常在一分钟内检查。`status` 只输出登录布尔状态、计数和脱敏错误，不打印 Cookie。未登录会报告 `needs_bilibili_login`，不会用标题或简介伪装“已看完／已学会”。

## 学习如何工作

用户已选择“按聊天中出现的兴趣选择内容”。思维核心在同一次回复中提取最多两个兴趣词和逐字证据，例如用户说“最近在学 Python 异步编程”。只有证据确实出现在当前用户文本中才记录；含密钥、Cookie、验证码等内容的输入不用于兴趣提取。图片模型的猜测、模型自己的话、回复风格要求和纯运维指令不应成为兴趣。

后台按兴趣搜索少量候选，每次选择一个未学习的视频读取字幕，再用现有语言模型整理来源笔记。默认间隔 1～3 小时，每天最多 6 次尝试，搜索每批最多 5 个候选，每个视频读取最多前 30 分钟、正文最多 2 万字符。日预算目前按 UTC 日计数，重启不清零。失败也占用当次尝试，避免认证或网络异常时不断请求。没有合适字幕就报告失败，不把简介当知识。已学视频按会话与来源 URL 去重。

也可以直接在 QQ 说：

- “学习这个视频 BV1……”，进入明确的视频学习队列。
- “查看学习状态”，查看工具返回的任务和知识计数。
- “查一下知识库里关于异步编程的内容”，检索已学资料。

以上需要模型选择对应的 `bilibili_learn_video`、`learning_status`、`knowledge_search` 工具。排队成功不等于学习完成。显式重复提交失败的视频可以重试；已完成来源不会重复入库。

知识保存在 `data/knowledge/knowledge.db`，不写入 MMTR 的身份块或经历通道。检索使用 SQLite FTS5，给中文构建字词索引，按会话隔离，并附带视频链接、字幕／AI 字幕／ASR 来源类别和可用时间点。每轮最多注入少量资料；它们只是视频中的说法，不是已经验证的事实，也不能覆盖人格或系统指令。这是检索式学习，不修改模型权重。

无字幕视频的本地 ASR 默认关闭。可按上游说明先准备 ASR，再明确设置 `learning.bilibili.fallback_to_asr: true`；本轮没有自动安装 ASR 模型。ASR 是识别字幕，语音合成任务仍待定。

## QQ 图片与表情包

OneBot 数组图片段和 CQ 图片段会被解析，纯图片消息也能进入思维核心。只有文件 ID 时会尝试 OneBot `get_image` 取图片 URL。下载限量、限制格式和像素；GIF 只取前三帧，按多个可见帧理解，不宣称看过完整动图。默认每条消息最多分析 2 张图片，原始图片不长期保存，仅缓存按会话隔离的描述与分类。

视觉结果包含图像类别、可见内容、图中文字，以及“图像表达的情绪＋可见依据＋模型置信度”。它不判断真实人物身份或真实心理状态，也不把表情包情绪直接写成用户情绪。图中文字作为外部材料，不执行其中的提示或命令。

使用现有 `selected_module.VLLM` 与 `VLLM` 配置。若密钥仍是占位文本，机器人会报告视觉模型未配置，不猜图。可在 `data/.config.yaml` 填入自己的视觉服务：

```yaml
selected_module:
  VLLM: MyVision
VLLM:
  MyVision:
    type: openai
    model_name: 你的服务实际支持的视觉模型名
    url: https://你的视觉服务/v1
    api_key: 你的私有密钥
```

自己部署 Qwen3-VL 时填写实际模型名和 OpenAI 兼容服务地址。没有自动假定机器具备运行视觉大模型所需的显存。

图片 URL 默认只允许公开地址，重定向和 DNS 解析同样检查；`file://` 和随意的本地文件不读取。如果 NapCat 通过明确的私有图片服务提供资源，可在 `visual_understanding.trusted_image_hosts` 加入它的确切主机名或 IP，随后重启。不能将任意 URL 当作本地资源读取。

学习 B 站视频时，会先检查评论结构中的图片字段和正文链接；如果上游 MCP 只返回评论文字，则再通过 B 站公开接口读取一页热门评论及回复中的图片地址。此补充读取不发送 Cookie，只访问固定的 B 站 API 域名；图片只接收 `hdslb.com` / `biliimg.com` 地址。每个视频默认最多识别 4 张，可在 `learning.bilibili.max_comment_images_per_video` 调整（上限 6）。图片沿用已配置的视觉模型，识别说明写入知识检索并保存到私有图片库；知识页面会显示评论与视频来源。评论图片的检索排序只比视频笔记高 5%，回复仍须结合当前语境决定是否引用。若 B 站不返回图片或公开接口暂不可用，则跳过图片，不影响视频与文字评论学习。

## 配置与关闭

`config.yaml` 中的 `learning`、`visual_understanding` 均默认开启接入；实际调用仍受账号和模型就绪状态约束。在私有配置中设相应 `enabled: false` 可关闭。知识库和媒体描述缓存已被 Git 忽略；备份需同时考虑 MMTR、认知状态和知识库三个独立数据库。

## B 站梗发现

学习服务启动后约两小时进行首次随机学习，之后按随机间隔继续；每次由模型结合近期对话和已学内容自由选择主题，不限制为梗或固定分类。搜索按 B 站结果顺序尝试新视频，不再因自设的主题相似度门槛拒绝结果；重复来源、临时跳过项和无法读取字幕的视频仍会跳过。随机学习每天最多收录 12 条视频。它只针对最近一次 QQ 私聊会话运行；明确排队的视频任务优先，随机学习优先于兴趣学习。普通随机学习写入视频与评论知识笔记；用户明确要求学习新梗时仍走梗提取流程。学习时会读取最多 20 条热门主评论及高赞回复；评论只作为观众观点和梗用法的独立材料，不当成视频事实。评论请求失败或超时不会丢弃已有字幕学习结果。搜索命中不会自动向 QQ 分享视频。主动对话可随机抽取已收录梗，只有语境自然时才引用；匹配的视频截图可以在主动对话中发送。

模型从带时间戳字幕和评论区分别提取梗词、释义、使用语境与逐字证据；程序要求梗词和证据确实出现在对应来源里。评论证据会标成观众评论且不虚构视频时间点；出处未在来源中交代时标为待考。每个梗按来源单独切块进入现有 SQLite FTS 索引；两分钟内识别到多个字幕梗时，汇总为视频内的梗密集片段。对话检索可自动召回，模型也可调用 `meme_knowledge_search` 单独查梗，结果带视频来源和字幕时间点（评论条目不带伪造时间点）。私有配置可用 `learning.memes.enabled: false` 关闭，或调整间隔与每日上限。

图片识别默认复用 `selected_module.LLM` 所选 DeepSeek 提供方的 API 地址和密钥，并用 `deepseek-flash` 识别收到的 QQ 图片。它会生成可见内容、情绪表达、意图、语境和适用场景标签。识别成功后，规范化图片与标签保存在本地知识库 SQLite 的 `meme_assets` 表内，仅保存 QQ 私聊收到的图片并按会话隔离，每个会话默认最多保留300张；同一用户后续聊天时，思维核心可在确实适合时调用 `qq_send_relevant_image` 检索并发送。原图不会上传给 QQ 以外的图片托管服务，但会随识图请求发送给 DeepSeek。备份或删除知识库数据库会一并备份或删除图片库；关闭视觉识别后不会继续识别或入库。

本轮另外修复了 OneBot 接收循环：模型／工具等待期间仍处理 echo 回包，防止连续发消息或获取图片时自己阻塞自己的确认。

## 验证边界

本地验证了固定 npm 包的真实 stdio 初始化和凭据状态接口；当前测试环境没有 B 站登录，返回未登录。自动学习、笔记、带时间点检索、会话隔离、缺字幕拒绝、重复去重、每日预算、图片／动图、错误视觉输出与 OneBot 回包并发使用可控测试数据和测试模型验证。真实已登录视频、云端视觉 API、QQ 图片下载链路和生产模型须在服务器补齐配置后实测。
