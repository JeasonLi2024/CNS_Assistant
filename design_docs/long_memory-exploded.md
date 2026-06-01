LangGraph / LangChain 官方文档：Checkpoint 与长期记忆膨胀治理方案
下面我直接按LangGraph / LangChain 官方文档 + 官方推荐架构，把「Checkpoint 膨胀」和「长期记忆（/memories/）膨胀」的官方建议方案分别讲清楚，不掺社区猜测，全部来自官方文档、LangChain 论坛、LangGraph 作者回答。

---
一、官方对「Checkpoint（会话历史 /state）膨胀」的建议
官方定位：Checkpoint 膨胀是生产环境首要风险，必须做硬限制。
1. 控制消息长度（messages 无限追加）
官方明确推荐三种方式：
（1）用 Reducer 截断旧消息（最基础）
在 State 里给 messages 加 reducer，超过 N 条就自动删最旧的：
def messages_reducer(old, new):
    combined = old + new
    return combined[-20:]  # 只保留最近20条
官方原话：Use reducers to delete old messages
（2）自动摘要（Summarization）（官方主推）
消息超过阈值（如 10 轮）→ 用 LLM 把早期消息压缩成一段摘要，替换旧消息：
- 保留语义、大幅减 token
- 官方教程直接给了完整代码：summarize_conversation 节点
（3）窗口化（Sliding Window）
只把最近 K 条消息放进 prompt，更早的只存 checkpoint、不进上下文

---
2. 控制 Checkpoint 快照数量（每步都存导致爆炸）
LangGraph 默认每一步都写一个 checkpoint，官方说这是 “time-travel 能力” 的代价，生产必须限制：
（1）max_versions：每个 thread 只保留最近 N 个 checkpoint
- 官方配置：checkpointer = PostgresSaver(..., max_versions=5)
- 只留最近 5 步快照，旧的自动删
（2）只在关键节点写 checkpoint（only_checkpoints）
graph.compile(
    checkpointer=checkpointer,
    only_checkpoints=["end_turn", "save_summary"]  # 非每步都写
)
官方建议：长流程不要每步都持久化
（3）定期 Pruning（定时清理）
官方强烈推荐后台定时任务：
- 每个 thread 只留最新 N 个 checkpoint
- 按 checkpoint_id（时间有序）删除旧记录
- 适用：MongoDB、PostgreSQL
（4）用 durability="exit"（只在会话结束写一次）
牺牲中途断点能力，只在一轮对话结束写 checkpoint，大幅减少写入量

---
3. 大 State 处理（图片 / 文件 / 长文本）
官方明确：不要把大二进制 / 长文本直接存 checkpoint
正确做法：
- 大文件 → 存 S3/OSS，state 只存 URL
- 长文本 → 存 LangGraph Store，state 只存 ID
- 避免 MongoDB 16MB 限制、Postgres TOAST 膨胀

---
二、官方对「长期记忆（/memories/）膨胀」的建议
官方定位：长期记忆是慢性膨胀，靠「Store + 向量检索 + 去重 / 过期」治理。
1. 必须用 LangGraph Store（官方长期记忆抽象）
不要自己搞文件目录，官方统一抽象：
- Store → 跨 thread、跨会话共享
- 支持：Postgres、Redis、向量库
- /memories/ 本质是 Store 的一个 namespace
2. 写入时：去重 + 摘要 + 结构化
官方防止 “每轮都存新记忆导致爆炸”：
（1）写入前语义去重
用向量检索查相似记忆，重复就不存：
existing = store.search(namespace, query=fact, limit=1)
if not existing:
    store.put(namespace, key, fact)
官方原话：deduplicate before saving
（2）记忆分块 + 摘要
- 单条记忆控制在 200–500 token
- 长内容拆成小块，或存摘要 + 原文引用
3. 读取时：RAG 检索，不把全量记忆塞进 prompt
官方最核心建议：长期记忆绝对不能全量加载
正确流程：
1. 用户提问 → 生成 query
2. 向量库检索最相关 3–5 条记忆
3. 只把这几条注入 system prompt
4. 全量记忆仍在 Store，不进上下文
4. 清理：TTL + 归档 + 合并
（1）TTL 过期
临时事实设有效期（如 30 天），自动淘汰
（2）定期合并（Merge）
后台任务：合并同类记忆、删除过时内容
（3）冷热分离
热记忆（常用）→ 向量库；冷记忆 → 归档库 / 对象存储

---
三、官方一句话总结（非常关键）
- Checkpoint 膨胀： 高频、致命、必须做 消息截断 / 摘要 + 快照数量限制 + 大文件外存
- 长期记忆膨胀： 慢速、累积、必须做 Store 托管 + 写入去重 + RAG 按需检索 + TTL 清理

---
四、官方推荐落地优先级（直接照做）
1. 先搞定 Checkpoint： 摘要 + 窗口化 + max_versions + 定时 prune（必须）
2. 再优化长期记忆： Store + 去重 + RAG 检索（生产必备）
3. 最后做数据分层： 大文件外存、冷热分离（规模化后）

---