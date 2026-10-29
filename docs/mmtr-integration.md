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
本次新增可选后端，不迁移、覆盖既有记忆，也不改本机私有配置。

## 启用

安装 Node >=22.13，确认 `node --version`，在本机 `data/.config.yaml` 写入：

```yaml
memory_backend: mmtr
mmtr:
  node: node  # 也可填 node 可执行文件的绝对路径
  database: data/memory/mmtr.db
  timeout: 10
  context_budget: 1200
  session_gap_seconds: 1800
cognitive_core:
  share_legacy_memory: true
```

然后按原方式启动 `app.py`。显式选择 mmtr 优先于旧的 `tencent_memory_enabled` 标志。
QQ/ESP 和嵌入式 Cognitive Core 通过同一个 MemoryManager 访问该库。
独立 `python -m cognitive_core.main` 同样识别配置并在退出时关闭桥接进程。
启动时 Node 不可用将明确报错，避免误以为持久化已经生效；运行期间检索失败返回空上下文，并将 adapter.degraded 标记为 true。

Python 适配器保留一个 Node 子进程，用锁串行化请求、用读取线程和超时防止调用永久挂起。
不开放网络端口，不启动 HTTP 服务，不需要另外运行 Node 守护进程。
进程关闭保留未结算的会话；重启后由 inactivity gap 决定是否结算上一场。
仓库现有统一 owner 模型保持一致：跨 QQ/ESP 身份共用 owner，独立嵌入 MMTR 时可通过 scope 隔离多用户。

## 语义提取的接入点

当前普通聊天提交使用保守 fallback：fact 保存这一轮对话，gist 取用户文本前 160 字符，affect 为空。
中控不会从模糊话语自动虚构人物、感情、承诺或到期日。要获得完整的仿脑行为，
调用方的 agent 应将其已确认的结构化观察送进：

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

回退时将 memory_backend 改回 legacy 或 tencent，mmtr.db 保留以供再次启用。
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
