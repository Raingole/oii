# MMTR 接入 OII

## 目录与边界

```text
serve/
├── core/memory/             # 中控统一记忆，新增 mmtr_memory.py 适配器
├── cognitive_core/          # 认知核心，通过 LegacyMemoryPort 共用中控记忆
├── controller/              # 事件、动作和调度
├── qq/                     # QQ/NapCat/官方机器人入口
├── mmtr/                   # 新增独立 MIT ESM 包，可单独复制嵌入任意 Node 应用
│   ├── index.mjs           # 数据、遗忘、关系、图检索、承诺、睡眠和巩固
│   ├── bridge.mjs          # Python 等宿主使用的私有 stdio JSON 协议
│   ├── test/               # Node 内置测试，无 npm 依赖
│   └── examples/           # 联想示例与规模测量
├── TencentDB-Agent-Memory/ # 既有的可选远程记忆服务
└── tests/test_mmtr_memory.py # 跨语言、中控和认知接口测试
```

MMTR 包不依赖 Python、腾讯服务或现有中控。中控本身仍沿用 requirements.txt 的依赖。
目前默认优先使用 MMTR，暂不启用腾讯记忆；旧腾讯数据保留，不自动迁移。
`mmtr.preferred: true` 优先于旧私有配置中的后端选择与腾讯开关。

## 启用

安装 Node >=22.13，确认 `node --version`，在本机 `data/.config.yaml` 写入：

```yaml
memory_backend: mmtr
tencent_memory_enabled: false
mmtr:
  preferred: true
  node: node  # 也可填 node 可执行文件的绝对路径
  database: data/memory/mmtr.db
  timeout: 10
  context_budget: 1200
  session_gap_seconds: 1800
cognitive_core:
  share_legacy_memory: true
```

然后按原方式启动 `app.py`。默认的 MMTR 优先策略会关闭腾讯记忆，并让认知核心共用同一记忆入口。
QQ/ESP 和嵌入式 Cognitive Core 通过同一个 MemoryManager 访问该库。
独立 `python -m cognitive_core.main` 同样识别配置并在退出时关闭桥接进程。
启动时 Node 不可用将明确报错，避免误以为持久化已经生效；运行期间检索失败返回空上下文，并将 adapter.degraded 标记为 true。

Python 适配器保留一个 Node 子进程，用锁串行化请求、用读取线程和超时防止调用永久挂起。
不开放网络端口，不启动 HTTP 服务，不需要另外运行 Node 守护进程。
进程关闭保留未结算的会话；重启后由 inactivity gap 决定是否结算上一场。
仓库现有统一 owner 模型保持一致：跨 QQ/ESP 身份共用 owner，独立嵌入 MMTR 时可通过 scope 隔离多用户。

## 语义提取的接入点

普通聊天现在通过同一次模型决策的 `memory_observation` 提取 fact 事实、gist 简短印象与主题、affect 情绪及其原文证据。辅助提取格式异常不阻断正常回复；没有模型提取时，适配器保留事实并生成保守的主题摘要。明确的沟通偏好有对应摘要，gist 不再直接等于原句前 160 字。

情绪通道仍有原文校验：只接纳当前用户文本中能确认的自述感受，保留逐字证据；中性、否定、提问、假设和他人引述不会因为模型的猜测被填成情绪。保守识别可能漏掉含蓄或未支持的表达，空通道表示未记录，而不是判断用户没有情绪。自动观察中的实体必须出现在当前原文；不自动创建承诺、日历和身份块。纯 `action_result` 回执只留在动作日志，不再制造“用户：action_result”的聊天记忆。主动思考不提取用户经历。

例如“以后少一点符号吧，然后句子短一点，看着舒服”可形成“用户希望少符号、简短回复”的事实、“偏好清爽沟通”的印象，以及带“看着舒服”原话的舒适感受。

调用方也可显式提交其已确认的结构化观察：

```python
backend = memory_manager.mmtr_backend
backend.remember({
    "scope": memory_manager.owner_id,
    "fact": "雨天一起在餐桌边包饺子，聊到十一点",
    "gist": "一起做饭很合拍",
    "affect": "安心，亲切",
    "entities": ["小雨", "饺子"],
    "themes": ["做饭"],
    "anchors": ["06-18"],
    "externalId": "observation:unique-event-id",
})
backend.call("touchSession", "qq:7", {
    "scope": memory_manager.owner_id,
    "entity": "小雨",
    "relationshipDelta": {"warmth": 0.04, "trust": 0.02},
    "summary": "谈到今年想学做饭",
    "openLoops": ["下次问他喜欢什么馅"],
})
backend.call("promise", {
    "scope": memory_manager.owner_id,
    "text": "明年生日一起包饺子",
    "due": "2027-06-18T18:00:00+08:00",
    "entity": "小雨",
    "gist": "生日约定",
})
backend.call("setCore", "user-name", "他的名字是小雨", {
    "scope": memory_manager.owner_id, "kind": "identity",
})
```

工具结果只处理显式的 `memory_observation` 对象，不自动保存整个工具返回值。
认知核心按现有 should_store 策略决定哪些事件值得长期入库；quiet 输出现在完整进入上下文，不会被旧版的 `-` 列表解析丢弃。
普通对话只在 MMTR 中留存，不再同步写入 legacy 的 memory_messages、memory_facts 等表。
MemoryManager 原有结构化 defaults/vault 仍属于原有应用功能，MMTR 单库承诺仅针对 mmtr.db。

## 维护、回退与验证

在宿主现有调度中定期调用 `backend.call("consolidate", {"scope": owner_id})`。
MMTR 不自行发送提醒：窗口内的承诺在下一次 recall 浮现，离线过期在下次 recall/keep/consolidate 结算。
若要主动提醒，应由应用调度取回结果并按现有发送机制处理。
关系 delta 在会话结束才结算，promise 的兑现／失约作为独立事件即时结算。

如未来明确需要切换后端，须先设 `mmtr.preferred: false`，再修改 `memory_backend`；使用腾讯时还需配置对应服务。当前配置保持腾讯关闭。mmtr.db 保留以供再次启用。
原有数据库不会自动转换成三通道模型；历史迁移需要显式的字段映射和提取，避免伪造情绪。
备份请在无写事务时复制 mmtr.db，或使用 SQLite 的备份机制。

```sh
node --test mmtr/test/memory.test.mjs
python -m unittest discover -s tests -p test_mmtr_memory.py -v
python -m unittest discover -s tests -p test_cognitive_core.py -v
python -m unittest discover -s tests -p test_controller_regressions.py -v
node mmtr/examples/dumplings.mjs
node mmtr/examples/benchmark.mjs 1000
```

测试结果与测量环境见 [验证记录](mmtr-validation.md)。

当前对话的真实召回和认知引用可在 [记忆观察台](mmtr-observer.md) 查看。
