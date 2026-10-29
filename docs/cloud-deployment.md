# 云端更新与自动拉起

适用环境：Linux + systemd + Git，建议 Python 3.10/3.11；MMTR 需要 Node >=22.13，腾讯 MemoryCore 需要 Node >=22.16。
既有依赖（如 ffmpeg、模型文件和对应平台编译工具）仍由服务器提供。启用 MailPilot 且需要构建时还需要 Go。
两个根目录入口由 Python 标准库调度器 `deploy/service_manager.py` 支撑。

## 已有服务器首次切换

先在旧启动终端按 Ctrl+C 停止旧脚本，再在服务器上的 **serve 项目目录** 执行：

```bash
git pull --ff-only origin main
sudo bash auto-start.sh install
sudo bash auto-update.sh install
```

如服务器没有 sudo、当前已是 root，去掉命令中的 sudo。
安装会读取既有 `data/.config.yaml` 和 `.env`；没有私有配置时会停止并提示。
先选 `.venv/bin/python`，其次 `venv/bin/python`；都不存在时在准备阶段建立 `.venv`。
可在 `.env` 中以 `PYTHON_BIN=/完整路径/venv/bin/python` 指定解释器。
用 sudo 安装时，Git 操作和依赖构建会交给项目目录所有者执行，避免产生 root 所有的虚拟环境文件。

首次切换前如云端已有 Git 跟踪文件的本地修改，请自行提交或备份。脚本不会自动 stash、覆盖或丢弃这些修改。
不要在更新命令前执行强制清理，以免误删部署文件。

## 后续手动更新

```bash
sudo bash auto-update.sh run
```

流程：获取 `origin/main` → 确认可以快进 → 给当前版本建立本地 `deploy-rollback-UTC时间` 标签 → 更新代码 → 按输入变化准备依赖 → 重启 `oii.service` 并等待启动就绪。
默认回退标签 `default-rollback-before-mmtr` 始终保留，不被更新脚本移动。
不会自动推送部署回退标签到 GitHub。

代码与依赖均无变化时不重启。`requirements.txt` 变化或首次准备时安装 Python 依赖；
MemoryCore 清单变化时安装并构建；MailPilot 源码变化时重新构建。
`INSTALL_PYTHON_DEPS=0` 可关闭 Python 自动安装，`1` 可强制安装，默认 `auto`。
`--no-deps` 仅跳过 Python 安装，`--no-restart` 暂缓重启，状态会留待下次完成。

依赖安装或重启失败时，不会清除未完成标记。修正原因后重新运行同一更新命令即可重试。
依赖安装失败时代码可能已经更新；回退标签保留原代码版本，虚拟环境和数据不属于 Git 标签备份范围。
私有配置、数据库、模型和 `.env` 都不会被强制清理，更新状态保存在忽略提交的 `data/deploy/`。

## 自动更新与自动拉起

`auto-start.sh install` 安装 `oii.service`，在开机后启动；受管进程异常退出时，整个服务组会在 5 秒后重新拉起。
启动时检查端口、进程和就绪状态，成功后才向 systemd 报告 READY；更新命令不会只凭进程创建就宣告启动成功。
重复运行安装命令可以刷新服务定义；不会创建第二套重复进程。

`auto-update.sh install` 安装 `oii-update.service` 和 `oii-update.timer`，默认每小时检查一次，带最多 5 分钟随机延迟。
服务器关机期间错过的定时检查会在启动后补跑。Git 认证必须预先配置好，定时任务不会等待交互登录。

```bash
# 查看运行状态、实时日志
sudo bash auto-start.sh status
sudo bash auto-start.sh logs

# 手动重启 / 停止 / 再次启动
sudo bash auto-start.sh restart
sudo bash auto-start.sh stop
sudo bash auto-start.sh start

# 查看下次自动检查时间、自动更新日志
sudo bash auto-update.sh status
sudo bash auto-update.sh logs

# 关闭自动更新定时器
sudo bash auto-update.sh disable

# 仅准备依赖或前台调试
bash auto-start.sh prepare
bash auto-start.sh check
bash auto-start.sh run
```

进程输出在 `tmp/services/controller.log`、`ui.log`、`memory-core.log`、`mailpilot.log`、`mcp-*.log`。
调度器本身的启动、重启、失败记录可通过上面的 journal 日志命令查看。
不自动接管旧 nohup/screen/手工启动的进程；若端口已占用，会提示先关闭旧实例。

## 服务选择

配置先合并 `config.yaml` 和 `data/.config.yaml`，私有值优先；`.env` 可设置以下部署开关：

```dotenv
# MMTR 优先策略开启时始终不启动腾讯服务，旧的强制开关也不覆盖此策略。
START_MEMORY_CORE=auto
START_UI=1
START_MCP=1
# 默认 auto：有 IMAP 用户配置，或显式指定 MAILPILOT_CONFIG 时才启用。
START_MAILPILOT=auto

UI_PORT=8010
# BACKEND_PORT 未设置时读取 server.http_port，默认 8003。
# BACKEND_PORT=8003
# NODE_BIN=/完整路径/node
# MAILPILOT_CONFIG=/完整路径/mailpilot-config.yaml
# MAILPILOT_BIN=/完整路径/mailpilot
# OII_UPDATE_CALENDAR=daily
```

更新 `.env` 后运行 `sudo bash auto-start.sh install`。改变定时日历后再运行 `sudo bash auto-update.sh install`。
MCP 端口可用 `MCP_PORT`（餐厅服务）和 `MCP_AGGREGATOR_PORT` 设置；其默认值分别为 8766、8765。
端口和服务名等设置在启动前验证，密码和 API Key 不写入生成的 systemd 单元。

清理了根目录的 `set-and-start.sh`、`setup-and-start.sh`、`start-all.sh`、`update.sh` 四个旧部署入口。
Android 的短信守护脚本和 `TencentDB-Agent-Memory/` 内部脚本是相应组件的程序资源，不作为云端入口清理。

## 验证范围

部署测试使用本地临时 Git 远端验证快进更新、自动回退标签、分叉／本地修改拒绝、未跟踪文件保护、失败重试和无变化不重启。
同时验证 Shell 语法、CLI、后端选择、依赖指纹、服务就绪失败和 systemd 单元生成。
当前工作机为 Windows；未在真实云主机安装 systemd 服务、下载完整模型或执行生产依赖安装。

验证结果：21 项部署测试通过，Python 全套 74 项通过，MMTR Node 测试 24 项通过；Shell 语法与入口帮助命令通过。

## MMTR 记忆观察台

更新并重启后访问 `http://服务器IP:8010/memory.html`（UI_PORT 可配置）。
获取访问密钥：

```bash
sudo bash auto-start.sh token
```

将命令输出的密钥填入页面连接框。密钥保存在私有目录或来自显式配置，不提交 Git。
目前默认优先 MMTR；旧腾讯开关与 `START_MEMORY_CORE=1` 不会重新启用腾讯。
完整说明及验证范围见 [记忆观察台](mmtr-observer.md)。
