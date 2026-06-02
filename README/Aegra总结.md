对 Aegra 源码、配置、架构的深入分析，全面总结如下：

---

## 一、任务调度（Job Scheduling）

**Redis 任务队列**
Run 请求到来后立即写入 Redis List，与 HTTP 请求生命周期完全解耦。Worker 通过 BLPOP 阻塞监听队列，按序取出任务异步执行。

**租约式崩溃恢复（Lease-based Recovery）**
Worker 取出任务后在 Redis 写入租约 Key，每隔 `HEARTBEAT_INTERVAL_SECONDS=10s` 续约一次。Reaper 进程每隔 `REAPER_INTERVAL_SECONDS=15s` 扫描所有租约，超过 `LEASE_DURATION_SECONDS=30s` 未续约的任务视为 Worker 崩溃，自动重新投入队列，最多重试 `BG_JOB_MAX_RETRIES=3` 次。

**Cron 定时调度**
基于标准 Cron 表达式调度 Agent 执行，使用分布式锁（`CRON_CLAIM_DURATION_SECONDS`）防止多实例重复触发，支持秒级调度（可选），单次批量触发数量受 `CRON_TICK_BATCH_SIZE` 控制。

**双模式执行**
- `REDIS_BROKER_ENABLED=false`：Dev 模式，进程内 asyncio 直接执行，无队列
- `REDIS_BROKER_ENABLED=true`：生产模式，Redis 队列 + Worker 进程池，支持横向扩容

---

## 二、流式数据处理（Streaming）

**8 种 SSE 流模式**
支持 `values`、`updates`、`messages`、`messages/partial`、`events`、`debug`、`custom`、`error` 八种模式，客户端可按需订阅不同粒度的执行数据。

**Redis Pub/Sub 跨实例推送**
多实例部署时，执行任务的 Worker 实例将每个 chunk 发布到 Redis Channel（以 `thread_id` 为 key），持有客户端 SSE 连接的实例订阅该 Channel 并转发，彻底解决多实例下流式数据孤岛问题。

**断线重连与事件回放**
Redis 缓存未消费的 SSE 事件，客户端断线重连后可从断点继续接收，不丢失中间输出内容。

**双执行接口**
- `POST /threads/{id}/runs/stream`：SSE 流式，长连接实时推送
- `POST /threads/{id}/runs`（wait 模式）：同步等待，图执行完毕后一次性返回

---

## 三、状态持久化（State Persistence）

**双连接池架构**
- SQLAlchemy + asyncpg：负责元数据表（assistants、threads、runs），应用层 ORM 查询
- psycopg + AsyncPostgresSaver：负责 LangGraph Checkpoint，图执行状态的精确持久化

**PostgreSQL Checkpoint**
每个 Run 执行过程中，LangGraph 在每个 super-step 边界自动保存 checkpoint（包含 channel values、pending writes、版本向量），服务重启后会话状态完整保留，支持从任意历史节点恢复。

**pgvector 语义存储**
Store API（`/store`）支持键值存储和向量语义搜索，底层使用 pgvector 扩展，可配置 OpenAI embedding 模型，实现跨 Thread 的长期语义记忆。

**连接池参数化**
```
SQLALCHEMY_POOL_SIZE=10 / MAX_OVERFLOW=20
LANGGRAPH_MIN_POOL_SIZE=5 / MAX_POOL_SIZE=20
```
两套连接池独立配置，按负载灵活调整。

---

## 四、图执行引擎（Graph Execution）

**Graph Factory Pattern**
通过 `@asynccontextmanager` 实现动态图构建，每次请求独立调用工厂函数，根据 `ServerRuntime` 中的用户身份和 Context 参数决定图结构（节点、边、工具列表），不同请求可获得结构不同的图实例。

**ServerRuntime 依赖注入**
工厂函数接收 `ServerRuntime[ContextType]` 参数，包含：
- `user`：已认证的用户对象（含权限列表）
- `context`：强类型的请求级配置（model、system_prompt、feature flags 等）
- `execution_runtime`：运行时环境引用

**Checkpointer 自动注入**
Aegra 启动时统一为所有图注入 PostgreSQL checkpointer，图开发者无需手动配置，保证所有 Run 的状态统一持久化到同一数据库。

**Human-in-the-Loop（HITL）**
基于 LangGraph 原生 interrupt 机制，图执行到审批关卡时自动暂停并持久化状态，等待外部信号（人工审批）后从断点精确恢复，Run 生命周期完整保留。

**MCP 集成**
在 Graph Factory 内部通过 `MultiServerMCPClient` 动态连接 MCP 工具服务器，工厂函数退出时自动清理连接（`finally` 块），防止资源泄漏。

---

## 五、认证与授权（Auth）

**插件化认证系统**
通过 `aegra.json` 中的 `auth.path` 配置自定义 Python 认证处理器，支持三种模式：
- `AUTH_TYPE=noop`：跳过认证（仅开发）
- `AUTH_TYPE=custom`：自定义 Python Handler（JWT / OAuth / Firebase 均可）
- 内置 JWT / Firebase 预置 Handler 可开箱使用

**请求级 Auth 中间件**
每个请求到达受保护路由前统一执行认证，公开端点（`/health`、`/ready`、`/info`、`/live`）无需认证，其余端点强制校验。

**权限透传到图内部**
认证结果（用户身份、权限列表）通过 `ServerRuntime.user` 注入到 Graph Factory，图内部可基于用户权限动态决定工具列表（如管理员才能访问的工具）。

---

## 六、可观测性（Observability）

**OpenTelemetry 链路追踪**
支持多目标同时扇出（`OTEL_TARGETS=LANGFUSE,PHOENIX,GENERIC`），内置 Langfuse、Arize Phoenix 和通用 OTLP 适配器，不绑定任何特定平台，Agent 内部每个节点的执行都有完整 span 记录。

**结构化日志**
- `ENV_MODE=LOCAL`：人类可读格式，适合开发调试
- `ENV_MODE=PRODUCTION`：JSON 结构化输出，适合日志收集系统（ELK、Loki 等）
- `LOG_VERBOSITY=verbose`：附带 request-id，支持单次请求全链路追踪

**Prometheus 指标**
`ENABLE_PROMETHEUS_METRICS=true` 后暴露 `/metrics` 端点，输出标准 HTTP 请求指标（延迟、吞吐、错误率），可接入 Grafana 构建监控看板。注意该端点无认证保护，需网络层隔离。

---

## 七、扩展能力（Extensibility）

**自定义 HTTP 路由**
通过 `aegra.json` 的 `http.custom_routes` 配置追加 FastAPI 路由，业务专属接口可与 Agent Protocol 接口共存于同一服务，无需单独部署。

**多配置文件支持**
优先级：`AEGRA_CONFIG` 环境变量 → `aegra.json` → `langgraph.json`（兼容回退），已有 LangGraph 项目无需任何改动即可接入。

---

## 技术栈全景

| 层次 | 技术选型 | 用途 |
|---|---|---|
| HTTP 框架 | FastAPI + Starlette | 路由、中间件、SSE、生命周期管理 |
| 图执行 | LangGraph（Python）| 状态图构建、节点执行、Checkpoint |
| 关系型存储 | PostgreSQL + asyncpg / psycopg | 元数据 + Checkpoint 双连接池 |
| 向量存储 | pgvector | 语义搜索、长期记忆 |
| 消息代理 | Redis（List + Pub/Sub + Key/TTL）| 任务队列 + SSE 跨实例 + 租约存储 |
| ORM | SQLAlchemy（async）| 元数据表的 ORM 映射 |
| 数据库迁移 | Alembic | Schema 版本管理 |
| 包管理 | uv | Python 依赖管理 |
| 可观测性 | OpenTelemetry + Prometheus | 链路追踪 + 指标采集 |
| 日志 | structlog | 结构化日志输出 |
| 认证 | 自定义 Python Handler | JWT / OAuth / Firebase |
| 工具协议 | MCP（Model Context Protocol）| 外部工具服务器集成 |
| CLI | aegra-cli（独立包）| 项目初始化、开发服务器、DB 迁移 |