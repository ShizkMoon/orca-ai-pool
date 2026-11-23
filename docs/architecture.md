# 号池架构 v4：单二进制对等网关

> 状态：设计稿，2026-11-19。取代 v3 的"CPA 保留 + rustpoold 旁路控制面"方案。
> 两条输入决定方向：底层换成更稳、更前沿的自研架构；控制面删掉——使用者不看它。

## 0. v3 → v4：什么被推翻了

v3 的立场是"数据面保守（CPA 不动），控制面激进"。两条实测事实让这个立场站不住：

1. **不稳定恰恰来自 CPA 这层**。发行版跟随（v8 config 布局漂移，升级是风险事件）、亲和/冷却状态是进程本地的（重启即丢）、C-ABI 插件改动必须重启（断流）、单点。保留它等于把稳定性的钥匙放在别人的发布节奏里。
2. **控制面没有消费者**。调和器、DuckDB 分析面、面板、指标，全部是"给人看"的组件；使用者的实际行为是收推送、不看面板。监督系统的价值 = 查看频率 × 发现提前量，查看频率为零时，正确形态是进程内自感知 + 推送，不是外置观测栈。

v4 的原则：**稳定来自结构，不来自监督。** 部件数压到 1，共享状态压到 0，切换事件压到 0，控制面删除。

```text
v2 现状:  nginx + CPA + 2×C-ABI插件 + opencode2api + Docker + cron   → 6 个部件 6 个故障域, 701MB/967MB 内存
v3 方案:  上面的 6 个 + rustpoold + DuckDB + SQLite + Litestream    → 11 个部件, 更多
v4 方案:  rustpool ×2 节点 (对等) + systemd                          → 1 种部件, ~60MB/节点
```

### 0.1 规模前提（2026-11-23 补）

两条使用前提定死方案边界，也回答"为什么敢全自研"：

- **自用/小范围（2–10 人）**。Devin 白嫖窗口是个人杠杆：协议漂移砸下来的最坏情况是"这个池子先不用了"，不是"断一条业务线"——自研 Devin 驱动的维护期望由此封顶。小规模同时解锁一揽子豁免：单文件配置、无租户隔离、管理面只开 localhost、推送代替计费。
- **高效前沿**。数据面全 Rust（axum/tower/hyper/rustls/arc-swap/governor），SSE 零拷贝直通，per-凭据长连接池，单进程 ~60MB。前沿的边界同样写死：QPS<1 时 io_uring、thread-per-core 这类深水区技术是装饰，不进栈；前沿性落在形状上（无状态对等、函数化亲和、socket activation），不在孤立的技术名词上。

Devin 驱动配一条逃生舱：上游驱动做成可插拔 trait，仓库常备一个 20 行 passthrough 驱动指向本地保留的 CPA 二进制。Devin 协议大改、自研没跟上时，一行配置切回 CPA 过渡，追平再切回——革命保留，尾部风险封死。

## 1. 总架构

```mermaid
flowchart TB
    subgraph CLIENTS["客户端 (Orca / pi)"]
        C1["端点列表: [A, B, Tailscale-B]<br/>连接失败自动切下一个"]
    end
    subgraph NODES["两节点对等 · 无主从 · 无切换事件"]
        subgraph A["节点 A"]
            SA["systemd socket activation<br/>:8317"]
            RA["rustpool (单进程)<br/>axum · 调度 · 3 上游驱动<br/>注入中间件 · 会计 · 告警"]
        end
        subgraph Bn["节点 B"]
            SB["systemd :8317"]
            RB["rustpool (同一二进制)"]
        end
    end
    subgraph SRC["构建与配置"]
        GIT[("git: accounts.yaml + prompts/")]
        CI["CI: cargo build --release<br/>(musl 静态单二进制)"]
        CFG["config.yaml 编译产物<br/>scp + SIGHUP 原子热载"]
    end
    subgraph UP["上游"]
        DEV["Devin OAuth"]
        GOG["OpenCode Go<br/>(per-key 出口代理)"]
        GOAT["GOAT"]
    end
    subgraph NOTIFY["推送 (无面板)"]
        NT["ntfy / Telegram<br/>只发 3 类: 凭据死亡 · 窗口告急 · 命中率塌陷"]
    end
    C1 --> SA & SB
    SA --> RA
    SB --> RB
    RA --> DEV & GOG & GOAT
    RB --> DEV & GOG & GOAT
    GIT --> CI --> CFG --> RA & RB
    RA -.推送.-> NT
    RB -.推送.-> NT
```

被删除的 v2/v3 组件及替代物：

| 删除 | 功能去了哪 |
|---|---|
| CPA (Go) | rustpool 上游驱动 + 调度器 |
| opencode2api | opencode-go 驱动内的请求变换 |
| nginx | rustls 进程内 TLS + governor 限流 |
| Docker/Compose | systemd 直跑静态二进制，无镜像无守护 |
| jailbreak C-ABI 插件 | 原生 Rust 中间件，配置热载生效 |
| cron + record_usage.py | 进程内会计（每请求落 JSONL） |
| v3 的 rustpoold/DuckDB/面板/调和器 | 不替代——删除；deploy 收敛为一条命令 |

## 2. rustpool 内部

```text
rustpool (单二进制, musl 静态, ~60MB RSS)
├── 入口        axum 0.8 + hyper 1 + rustls (TLS 直收, :8317)
├── 限流        governor (GCRA, 进程内, 替代 nginx limit_req)
├── 鉴权        主 key / 客户端 key 列表, 常量时间比较
├── 中间件栈    tower Service 组合:
│     alias 还原 (-jailbreak/-nsfw 后缀剥离)
│     → 破甲注入 (profile 提示词, 语义照 docs/jailbreak.md)
│     → Devin 敏感词零宽混淆 (可选)
│     → 调度分发
├── 调度器      加权 HRW 哈希 (session→credential 纯函数)
│               + 本地冷却 (429/5xx 分级) + 5h 窗口计数
├── 上游驱动    devin (OAuth token 使用/刷新, 兼容读 auths/*.json 与 .cds)
│               opencode-go (per-key egress 代理 + 请求变换)
│               goat (openai-compat 直连)
│               SSE 直通: 边收边转发, 首字节即回写
├── 会计        环形缓冲 + 每请求 JSONL append (脱敏 key) + 3 条告警规则
├── 告警        进程内定时评估 → ntfy/Telegram 推送
├── 配置        arc-swap: SIGHUP → 解析 → 原子换指针, 进行中请求走旧配置
└── 管理        /healthz /config (仅 localhost) — 没有 CPAMC 面板
```

### 2.1 调度：为什么无状态反而更好

v2/v3 的会话亲和是 CPA 进程内的一张表——重启丢失、节点间不共享、需要故障转移编排。v4 换成纯函数：

```mermaid
sequenceDiagram
    participant C as 客户端
    participant N as 任一节点
    participant S as HRW(session_id, 池)
    participant U as 上游凭据
    C->>N: POST /v1/chat (带 session_id)
    N->>S: 加权最高分哈希
    S-->>N: 凭据 k (两节点同配置 → 同结果)
    N->>N: k 在冷却? 顺延下一候选, 记一次失败
    N->>U: 请求 (SSE)
    U-->>N: 流式首字节
    N-->>C: 直通转发
    N->>N: 会计 append + 环形缓冲 (命中率/窗口)
```

- 亲和 = hash(session_id, credential_id) 的加权最高分。同一会话在任一节点都解析到同一凭据，prompt cache 前缀天然保持（现状 98.5% cache read 的命根子）。
- 冷却：节点本地学习。B 节点不知道某号 429 了？它自己撞一次 429 就知道了，收敛代价 ≤2 个请求；额度的事实源本来就是上游，不是网关。
- 结果：没有亲和表、没有状态复制、没有脑裂、没有 failover 事件。节点是牲畜：加第三节点 = 装二进制 + 客户端列表加一行。

### 2.2 稳定性机制对照

| 机制 | 实现 | 修掉的 v2 痛点 |
|---|---|---|
| 零停机升级 | systemd socket activation：exec 期间新连接在 accept 队列等待，旧进程 graceful drain | 二进制升级 = 断流 |
| 一切热载 | SIGHUP + arc-swap 原子换配置（含破甲 profile、权重、密钥） | 插件/配置改动要重启 |
| 故障域 | 每节点 1 个进程 | 6 个部件 6 个域 |
| 内存余量 | ~60MB vs 现状 701MB/967MB | OOM 余量 10 倍 |
| 无发行版跟随 | 升级节奏归自己，重启即原子切换 | CPA v8 布局漂移 |
| 客户端双通道 | 端点列表 [公网A, 公网B, Tailscale-B]，TCP 连接失败自动下一个 | DNS/公网单点 |
| 部署原子性 | 单文件 scp + SIGHUP；GET /config 即运行真相，diff 即审计 | 配置漂移（服务器跑未提交 glm-5.3 那次） |

## 3. 观测：只留推送，不留面板

使用者的行为模型是"出事找我，没事别烦"。全部观测收敛为进程内 3 条规则：

| 规则 | 触发 | 动作 |
|---|---|---|
| 凭据死亡/到期 | 探活 401/403，或 Devin token 距到期 ≤7 天 | 推送（唯一需要人工动作的事：轮换 token） |
| Go 窗口告急 | 5h 窗口用量 > 80% | 推送（补号决策） |
| 命中率塌陷 | 1h 滑窗 cache_read/input < 60% | 推送（亲和被打破 = 成本异常） |

会计数据仍然全量落 JSONL（脱敏），但没有任何查询面——需要查的时候 grep / python 一次脚本的事，不为"也许要看"常驻一套分析栈。

## 4. 可定制

| 想改什么 | 动哪里 | 生效 |
|---|---|---|
| 权重/冷却/窗口参数 | accounts.yaml → 编译 | scp + SIGHUP，秒级，零断流 |
| 破甲 profile / 提示词 | prompts/（注入逻辑是原生中间件） | 同上——v2 里要重启的事在 v4 是热载 |
| 新 openai-compat 上游 | accounts.yaml 加一段 | 同上 |
| 新 OAuth 类上游 | 加一个 driver 模块 | 发版（socket activation 零停机） |
| 告警阈值 | 配置文件 | 热载 |

deploy 是一条命令：`poolctl deploy` = 编译（本地或 CI）→ scp 二进制与配置 → SIGHUP。没有守护进程在后台调和——运行配置文件就是部署产物本身，GET /config 返回它，漂移检查是 `diff` 一条命令。

WASM 插件位（P2 可选，默认不做）：破甲注入可以做成 wasmtime 加载的 .wasm 组件，换提示词引擎不重编译二进制。先不做：原生中间件已覆盖需求，wasmtime 换来 15MB 体积与组件模型复杂度，等真出现"第三方写插件"的场景再开。

## 5. 技术选型

| 位置 | 选择 | 弃用 | 理由 |
|---|---|---|---|
| 框架 | axum 0.8 + hyper 1 + tower | Pingora | 需要完整请求体改写（注入中间件），tower 的 Service 组合就是这个形状；Pingora 为纯 L7 代理的 CDN 场景优化，正文变换不是它的强项 |
| TLS | rustls (+ rustls-acme, P2) | nginx 终止 | 少一个进程；ACME DNS-01 进程内自动化 |
| 限流 | governor (GCRA) | nginx limit_req | 进程内，令牌桶语义等价 |
| 热配置 | arc-swap | etcd/任何配置中心 | 无锁指针换，读端零开销，无守护进程 |
| 运行时托管 | systemd + socket activation | Docker/k3s | 无镜像/注册表/容器运行时；静态 musl 二进制 scp 即部署 |
| 会计 | JSONL append + 文件滚动 | DuckDB/ClickHouse | 没有查询消费者；grep 够用 |
| 告警 | 进程内定时器 → ntfy | Prometheus/Grafana 全家 | 推送 3 条规则，不需要时序库 |
| 调度 | 自实现加权 HRW (~200 行) | CPA 调度器 | 纯函数、可单测、跨节点一致 |

## 6. 迁移：怎么在换发动机时不掉高度

替换的是活的生产链路，安全网分四层：

```mermaid
flowchart LR
    S1["P0 shadow<br/>rustpool 跑 :18318<br/>evals 全套 + 合成探针"] --> S2["P1 补 Devin 驱动<br/>+ 注入中间件 + 告警"]
    S2 --> S3["canary<br/>自己的客户端切过去跑一天<br/>消费者只有自己 = 零风险灰度"]
    S3 --> S4["切端点<br/>客户端模板 [A,B]"]
    S4 --> S5["CPA 栈热备 2 周<br/>一条命令可切回"]
    S5 --> S6["退役: compose/nginx/cron 下线"]
```

移植清单（工作量大头，诚实估算）：

| 块 | 来源 | 估时 | 风险 |
|---|---|---|---|
| opencode-go + goat 驱动 | opencode2api 的变换逻辑（仓库内已有） | 3–4 天 | 低——纯 openai-compat，tests/ 可迁移 |
| 调度器 + 冷却 + 窗口 | CPA 语义 + docs 描述 | 2 天 | 低——纯函数，单测全覆盖 |
| 破甲注入中间件 | jailbreak-inject (Go) 按 docs/jailbreak.md 语义重写 | 2–3 天 | 中——注入语义有评测兜底（evals/） |
| Devin 驱动 | 读 CPA 上游源码，port OAuth 刷新与请求形状，兼容 auths/*.json + .cds | 2–5 天 | 最高——配 0.1 的逃生舱，一行配置可切回 CPA 过渡 |
| 会计 + 告警 + 部署命令 | record_usage.py 语义 | 1–2 天 | 低 |

总量 3–5 周（单人，非全职）。目标代码量 ≤5k 行 Rust——替换 6 个部件，这个预算内可审。

## 7. 路线图

| 期 | 范围 | 验收 | 估时 |
|---|---|---|---|
| P0 | rustpool 骨架：axum + 调度 + opencode-go/goat 驱动 + SSE 直通 + 会计；shadow :18318 | evals 全套通过；探针延迟与 CPA 持平（±20%） | ~1 周 |
| P1 | Devin 驱动 + 破甲注入 + 3 条告警 + deploy 命令 | canary 客户端跑 24h 无人工干预 | 1–2 周 |
| P2 | 客户端模板切双端点；B 节点对等上线；CPA 退役；ACME | 拔 A 节点网线，客户端无感切换 | 2–3 天 |
| P3（可选） | WASM 插件位；渲染逻辑 Rust 化；第三节点 | — | 按需 |

## 8. 不做与边界

| 不做 | 理由 |
|---|---|
| 控制面/面板/指标库 | 无消费者；推送覆盖全部需要人响应的事件 |
| 共享状态存储 (Redis/etcd) | HRW 纯函数让两节点无需任何共享即可一致 |
| failover 编排 | 没有"切"这个事件——节点对等，客户端连接失败自动下一个 |
| WASM 插件（P2 前） | 原生中间件已覆盖；等第三方插件需求出现再开 |
| k8s/服务网格 | 无对应问题 |

边界声明：此设计为 2–10 用户、日千级请求优化。到 100+ QPS、多租户计费、或需要按 key 配额隔离时，重新评估调度与会计形态——那时加的是隔离层，不是推翻本架构。
