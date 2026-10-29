# Memorable Memory To Remember (MMTR)

一个可嵌入 agent 的仿人脑记忆层：事实先淡去，感觉慢一点，情绪最后离开。
MIT，零第三方依赖，ESM，无安装、编译或服务端要求。Node **>=22.13.0**，使用内置 `node:sqlite`。
22.5–22.12 虽然已经有 SQLite，但需要实验参数，本包选择 22.13 作为直接运行的下限。
兼容性依据：[Node 22 SQLite 文档](https://nodejs.org/docs/latest-v22.x/api/sqlite.html)。

```js
import MemorableMemory from './index.mjs';

const memory = new MemorableMemory('./memory.db');
memory.setCore('user', '他的名字是小雨');
memory.remember({
  fact: '雨天在餐桌边一起包饺子，聊到十一点。',
  gist: '一起做饭很合拍',
  affect: '安心，亲切',
  entities: ['小雨', '饺子'], themes: ['做饭'], anchors: ['06-18'],
});
const promise = memory.promise({
  text: '明年生日还来我家一起包饺子。',
  due: '2027-06-18T12:00:00+08:00',
  entity: '小雨', gist: '生日约定', anchors: ['06-18'],
});
console.log(memory.recall('包饺子')); // 默认 quiet：一小段不带内部评分的印象
memory.keep(promise);              // kept；重复调用不会重复奖励
memory.consolidate();
memory.close();
```

运行验证和示例，无需 `npm install`：

```sh
cd mmtr
node --test
node examples/dumplings.mjs
node examples/benchmark.mjs 1000
```

## 记忆的结构

每段经历持有三条独立通道，即使某条没有内容也保留其结构。

| 通道 | 半衰期 | 地板 |
| --- | ---: | ---: |
| fact 事实 | 4 天 | 0.02 |
| gist 感觉／概括 | 12 天 | 0.15 |
| affect 情绪 | 180 天 | 0.55 |

强度 `floor + (1 - floor) × 2^(-ageDays / halfLife) + decayingBoost`，输出限制在 `[0,1]`。
半衰期作用于地板以上的部分。检索回血也按各通道的半衰期衰减。
重要性由写入方给定，检索永远不修改它。

巩固时，年龄达到三倍半衰期且强度低于 `floor + 0.13` 的普通通道可以压缩：fact 清空，gist 至多 160 字符，affect 至多 240 字符。
原始 fact 不在另一张日志或 embedding 文本中留副本。调用方应给 gist 提供抽象概括，而非复制 fact。
旧的摘要和没说完的话作为普通记忆继续衰减；已结束会话的临时摘要会清除。

图含 `entity`、`temporal`、`calendar`、`theme` 四类无向边。时间邻近为两小时以内；
每条新记忆每类最多连接 16 个已有邻居，避免共同人物或同一会话形成平方级稠密图。
边的半衰期为 90 天，低于 0.01 时清除。扩散默认两跳，最多五跳，每层最多 64 个入口。

## 提取和向量入口

可以直接传入 `fact/gist/affect/entities/themes/anchors`，或为构造器提供同步 `extract(text)` 回调。
仅传 `text` 时，内置保守规则保存事实、取首句最多 160 字符作为 gist，affect 留空。
它**不会自动理解所有自然语言中的情绪、人物、相对日期和承诺**。需要调用方的 agent 提取这些字段。
不把“我记得”“明天见”之类模糊说法擅自升格为核心事实或正式承诺。

默认向量是支持中文字符和二元组合的 192 维特征哈希，完全离线，属于词法入口，**不是语义 embedding 模型**。
需要语义召回时，注入同步 `embed(compactText) => number[]`，或在宿主侧缓存模型生成的向量。
同一数据库应持续使用同一向量模型及维度。外部模型的依赖由宿主承担，包自身仍然零依赖。
向量只针对 compact gist（至多 2000 字符）及实体、主题，不对全部经历原文做 embedding。

```js
const memory = new MemorableMemory('memory.db', {
  extract: text => myAgentExtractedObservation(text),
  embed: compactText => myCachedEmbedding(compactText),
  clock: () => Date.now(),
  sessionGapMs: 30 * 60_000,
});
```

检索顺序：向量召回 → 沿边扩散 → 日历唤醒 → 重排 → MMR 多样性裁剪 → 预算组装。
日历锚点使用 `MM-DD` 和 UTC 日历，前后三天唤醒，跨年和闰日均显式处理。
如业务采用本地日历，应在宿主侧统一日历时区；承诺截止时间使用带时区的 ISO 时间或毫秒时间戳。

防止自增强：回血 `0.08 / (1 + recallCount)`、累计 boost 至多 0.15、上一轮使用的记忆下一轮乘以 0.35，重要性不变。
只对实际装入上下文的记忆回血。quiet 是普通文字，不附加数字评分、通道标签或系统时间戳；原始内容中的日期和数值会保留。
full 返回 `{context, core, items, omittedReminders, pipeline}`，供调试查看通道强度、关联原因和承诺状态。

## 关系、睡眠和巩固

关系六维值域 `[0,1]`：`closeness` 亲近、`trust` 信任、`playfulness` 俏皮、`warmth` 温度、`tension` 张力、`familiarity` 熟悉。
默认基线分别为 `0.2/0.5/0.2/0.5/0.1/0.1`。构造器 `baseline` 可覆盖。

```js
memory.touchSession('qq:7', {
  relationshipDelta: { warmth: 0.06, trust: 0.02 },
  entity: '小雨',
  summary: '谈到今年想学做饭',
  openLoops: ['下次问他喜欢什么馅'],
});
memory.endSession('qq:7'); // 结算关系并将摘要、未完话题写入普通记忆
```

每个会话每维累计变更限于 ±0.12；正向变更再乘 `(1 - 当前值)`，负向乘当前值。
普通关系回归基线的半衰期 120 天，张力为 7 天。可通过构造参数调整。
每轮只积累 pending delta，`endSession()` 才结算；重复结束无效。
新消息／检索超过 sessionGap 时，先睡掉上一场，再创建新会话。等于间隔时仍算同场。
进程关闭和重新启动不构成会话边界，pending 和 last 均保存在 SQLite。

`consolidate({scope})` 在一个事务里回归关系、衰减边、压缩普通记忆、结算失约、体检日历。
可在宿主的低峰定时调用；**库本身不启动后台计时器**。检索和 `keep` 也会判定过期承诺，
因此第一次后续使用就能结算；没有任何调用时不会主动发送通知。

## 承诺与常驻区

`promise({text,due,entity,graceMs,windowMs,...})` 入库锁定原文。默认提醒窗口 7 天、宽限期 1 天。
权重 `0.2 + 0.8 × exp(-max(0,due-now)/window)`，越接近到期越清晰。
提醒窗口内不依赖查询相关性，优先进入上下文。超出 `due + grace` 才 broken；
失约的原始关系变更为信任 -0.1、温度 -0.06、张力 +0.1，兑现为 +0.03/+0.02/-0.02，均经过速率限制。
状态变更与关系代价同事务、只发生一次。超宽限期后调用 keep 不能绕过失约判定；终态 kept/broken 不互相改写。
承诺的所有通道与原文均不压缩，pending 检索使用上升曲线。

`setCore(key,content,{kind})` 常驻块只接受显式的 identity、preference、agreement，拒绝明显事件文本。
规则是保守防线，宿主仍应确认身份型事实；并非自然语言分类器。
不衰减、不压缩，每次完整放入上下文。每 scope 最多 4000 字符。
每次 recall 的 budget 是字符数（默认 1200），不是 token 数；若预算容不下全部 core，会明确报错。
承诺也绝不截断；放不下的提醒 ID 在 full.omittedReminders 中返回，调用方可增加预算或单独展示。

## API 与隔离

所有写入对象或方法选项可传 `scope`，默认 `default`。各 scope 的检索、边、关系、会话、承诺、常驻块隔离。
同一 scope 的多平台可共享记忆，以 `stream` 区分会话。跨 scope 不会因同名人物建边。

| 方法 | 作用 |
| --- | --- |
| `remember(experience)` | 写入经历；externalId 可实现去重，重复 ID 返回原记录 |
| `getMemory(id,{scope})` | 读取当前通道强度与承诺状态 |
| `recall(query,{scope,stream,style,budget,limit,hops})` | 返回 quiet 文字或 full 诊断 |
| `promise(input)` / `keep(id,{scope})` | 创建、兑现承诺 |
| `setCore` / `coreMemory` / `deleteCore` | 管理常驻块 |
| `touchSession` / `endSession` | 会话观察、睡眠 |
| `relationship(entity,{scope})` | 查看随时间回归后的六维关系 |
| `consolidate({scope})` | 巩固、到期判定 |
| `deleteMemory(id,{scope})` | 显式删除，含承诺、通道、边的级联清除 |
| `health()` / `close()` | SQLite 检查、关闭连接 |

数据集中在一个 SQLite 文件，DELETE 日志模式；写事务期间 SQLite 可能产生短暂 journal 文件，提交后无需携带旁路文件。
`:memory:` 可用于测试。同步 API 适合个人 agent 和中小规模数据，向量召回线性扫描，不声称替代生产数据库或 ANN 引擎。
实际延迟请用 benchmark 在部署机器测量。多写入者由 SQLite 锁和 5 秒 busy timeout 协调。
库不提供人格、提示词策略、聊天原文归档、HTTP 服务或主动消息发送。

本目录 MIT 授权仅覆盖 MMTR 新增代码，不重新授权仓库中既有项目或第三方子目录。
