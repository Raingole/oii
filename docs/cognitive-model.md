# Cognitive Core 模型

Cognitive Core 将现在的结构化 Self、当前 Emotion、过去 Memory、Goals、Relationship、Body、Appraisal、Meaning、Planning 和 Action 分开保存。Self/Emotion/Goal 是 SQLite 中的当前状态；历史事件、反思和长期经历通过 MemoryAdapter 进入长期记忆；Prompt 只生成运行时快照，不是状态数据库。

一次 Cognitive Tick：

`Event → load Self → semantic recall → cognitive rerank/context budget → Appraisal → Emotion delta + decay → Meaning → Goals → Planner → Decision/Action → Controller executor → result Event`。

Emotion 是带范围的向量，不是字符串。Personality trait 同时保存 base/current/confidence/evidence，当前实现没有把单条事件直接写成人格变化。`do_nothing`、`wait` 是合法决策。高风险 Action 的构造函数会拒绝缺少 controller approval 的动作。

Autobiographical/timeline 组件以事件证据为入口；不得从模型文风推断过去。BodyRegistry 将 ESP32 注册为 self-owned interface，空调仍是外部可控对象，QQ 是社交接口，天气是信息工具。
