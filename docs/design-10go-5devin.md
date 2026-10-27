# 8–10 OpenCode Go + 5 Devin Pro：号池深度设计

> 设计稿 v1 · 基线 commit `4f43e73` · 所有"已核对"结论都指向上游源码行号，未核对的一律标注"待验证"。
> 目标读者：维护这个池的我们自己。读完应该能直接开工，不需要再问背景。
>
> **v2 补章（§12）**：生产环境暂无静态代理、10 个 Go 全走支付宝、5 个 Devin 全走同一张汇丰香港万事达卡。约束变了，§12 重算了风险模型与出口方案，§6/§9 的相关条目以 §12 为准。

---

## 0. 结论先行

十加五的目标形态是 **10 个 OpenCode Go + 5 个 Devin Pro，一个公网口，两个人多 agent 重度跑**。
决定成败的只有三件事，其它都是配套：

1. **缓存命中率**。号数不是容量单位，命中率才是（95% → 0%，同样用量要 38 个号）。
2. **会话粘性两跳都要成立**。CPA 粘 Devin 凭据，oc2api 粘 Go key；断一跳，上游 prefix cache 全废。
3. **封号不可逆且会连坐**。所有性能优化都必须给"IP 不漂、号不关联"让路。

推荐路线：**保持现有分层（nginx → CPA → opencode2api → 上游），只补三块真缺口**——
入口在途合并、冷却梯度、号级出口隔离。不重写架构，因为 5 Devin Pro 必须留在 CPA 里（OAuth + 凭据级模型排除）。

**v2 前提**：10 个号今天全部从新加坡同一台机器、同一个公网 IP 出网，没有静态代理；Go 全走支付宝、Devin 全走同一张卡。在这个前提下，“防封”的第一矛盾从“IP 漂移”变成“**同 IP + 同支付渠道的双重关联**”，§12 专门处理。

---

## 1. 现状盘点：今天实际能配出什么

先把"配了就生效"和"配了不生效"分开。下表每条都已对上游源码核对。

| # | 能力 | 结论 | 证据 |
|---|---|---|---|
| 1 | CPA → openai-compat 上游注入会话头 | **不支持**。只注入 `prompt_cache_key`（且要 `support-prompt-cache-key: true`） | `internal/runtime/executor/openai_compat_executor.go:975-1015` |
| 2 | oc2api 的会话粘性来源 | 有会话头就用，否则回落 `conversationSeed(系统提示 + 首个用户轮)`；再不行 `previous_response_id`；再不行随机 | `internal/identity/request.go:71-110` |
| 3 | oc2api key ↔ 代理绑定 | 启动时按 `i % len(proxies)` **确定性绑定**，只在代理故障时迁移到最空的一个，恢复后迁回 | `internal/gateway/pool.go:637-643, 651-674` |
| 4 | 滚动代理 `#pool` | 每次重试**重新抽一次出口**。这不是抖动，是设计如此 | `internal/config/config.go:238-250` |
| 5 | CPA 每凭据并发上限 | **单容器没有**。`credential-concurrency` 只服务 Home 集群 | `internal/home/concurrency_release.go` |
| 6 | oc2api `prefer` | 只决定 zen/go 两层的先后，**go 用尽不会跨到 zen** | `internal/gateway/upstream.go:892` 附近 |
| 7 | 额度口径 | `/usage` 返回订阅级百分比，**不按模型、不可相加** | `opencode2api#docs/management.zh-CN.md` |

### 1.1 由上面推出来的四个判断

- **跨层会话粘性不靠 header 也成立**。单 CPA key + 稳定 system prompt 时，oc2api 的 `conversationSeed` 会算出同一个 session，Go key 就粘住了。想更稳，就把客户端 system prompt 冻住——这本来也是缓存的前提。
- **代理绑定是声明式的**。`proxies: [a, b, c]` 意味着 key#1→a、key#2→b、key#3→c、key#4→a……只要顺序不变，每天都是同一套映射。这是本项目防封的地基，不要动。
- **高并发不是配置项**。它是"入口在途合并 + 连接池 + 冷却梯度"三者配合出来的结果，单改一个参数只会把 429 从上游搬到本地。
- **三种上游的瓶颈不在同一维度**：Go 撞月额度（成本），Devin 撞免费池并发（号数），GOAT 撞 5h 窗口（短时突发）。设计要分开处理，不能一把梭。

### 1.2 现在配了但没生效 / 写错了的四处

| 位置 | 现状 | 问题 |
|---|---|---|
| `routing.session-affinity-subagents: false` | 子 agent 不继承父会话凭据 | **与缓存目标相反**。默认应为 `true`：子 agent 前缀通常就是父会话前缀 |
| `routing.retry.max-retry-credentials: 0` | 语义未核实 | 若是"禁止换凭据重试"，就等于关掉了整个池的容错，必须确认 |
| `requests.passthrough-headers: false` | 注释写成"请求头透传" | 该字段实际控制**上游响应头**下发（`internal/config/sdk_config.go:102-104`）。管理面 SSE/`Retry-After` 想透传就得开 |
| `opencode2api.monitoring.refresh_seconds: 60` + 插件 60s | 两个轮询器都打 `/usage` | 10 个 key × 2 = 20 次/分钟的重复上游请求，应二选一 |

---

## 2. 不变量（按优先级，冲突时上位胜）

1. **封号安全**：单号单 IP 永不漂移；号与号之间不共享支付、设备、邀请链。
2. **缓存命中**：同一会话的请求前缀在整段会话内不变，且永远落到同一个上游账号。
3. **单请求延迟**：P50 不因池子变大而变差。
4. **综合吞吐**：并发上不去时可以慢，不能错。
5. **成本**：在前四条都满足后，再谈省。

—— 一个直接的推论：**入口的限速与并发闸门只作用于新请求，不作用于进行中的流**。任何会掐断 SSE 的限流都是错误设计。

---

## 3. 三个方案

### 方案 A：分层纵深（推荐）

保留 nginx + CPA + opencode2api 三层，各自补一块职责：

- **nginx**：连接/缓存层。加 `keepalive` 到上游、`least_conn`、幂等请求的在途合并（`proxy_cache_lock`）、`/v1/models` 短缓存；**不加**对 `/v1/chat/completions` 的速率限制。
- **CPA**：身份/路由层。会话身份统一、Devin 侧冷却分级、子 agent 继承亲和、`rebind-on: quota`。
- **opencode2api**：Go key 层。给每个 server key 一个"会话桶上限"（压缩并发时允许串行化，不返回 429）。

**优点**：改动集中在配置生成器（poolctl）和 nginx.conf，不碰任何 Go 源码；每层职责单一，出问题能一条命令定位（`18317` 绕入口）。
**缺点**：三层都要改，一次要动三个文件；跨层指标要拼（`/api/monitor` + CPA usage + nginx `$upstream_cache_status`）。

### 方案 B：单进程自研

写一个 Go 网关，把三层逻辑塞进一个进程：统一 session id 生成、统一 key 池、统一缓存/合并。

**优点**：会话身份终于有唯一真相；缓存/合并/冷却都在内存里，延迟和可观测性最好。
**缺点**：Devin OAuth 那一坨（token 刷新、凭据级 excluded_models、`request-scoped-errors`）要重新实现，等于把 CPA 抄一遍；CPA 迭代很快，抄完就开始漂移。**5 Devin Pro 是硬约束，它把方案 B 的收益吃掉了一半。**

### 方案 C：薄网关（CPA 直连 Go）

去掉 opencode2api，CPA 直接用 `openai-compatibility` 打 `https://opencode.ai/zen/go/v1`，一个 Go key 一个 entry。

**优点**：少一跳，少一个容器，少一处配置双写。
**缺点**：丢三层能力——Go key↔代理绑定、`conversationSeed` 会话粘性、跨协议转换（anthropic/responses 入口）。要自己用 CPA 的 `weight` 手工近似，且每个 key 得单独配 `proxy-url`。

### 选 A，理由

5 个 Devin Pro 只能在 CPA 里（OAuth 刷新 + 凭据级模型排除是 CPA 原生能力，换任何方案都得重写）；
10 个 Go key 的绑定和协议转换只能在 opencode2api 里（已验证 637-643 行的确定性映射）；
两者之间的缝隙用 nginx 补最便宜——**缓存是纯 nginx 能力，不需要任何代码**。

---

## 4. 落地阶梯

按"每步都能单独回滚、单独验收"切。

### P0 正确性（半天，先做，否则后面全白测）

| 动作 | 文件 | 验收 |
|---|---|---|
| `session-affinity-subagents` 改 `true` | `scripts/poolctl.py` `_build_cliproxy_config` | `grep session-affinity-subagents cliproxy/config.yaml` → true |
| `rebind-on: quota` | 同上 | 408/5xx 不把会话从原凭据上踢走 |
| `render` 硬拒 `#pool` 代理 | `scripts/poolctl.py` 校验层 | 写 `socks5://x#pool` 时 render 退出码非 0 |
| 核实 `max-retry-credentials` 语义 | 上游 `config_types.go` | 结论写进 README，或改成一个有依据的值 |

### P1 缓存与入口（1–2 天，收益最大）

| 动作 | 落点 | 预期 |
|---|---|---|
| nginx `upstream` 块 + `keepalive 64` | `nginx/nginx.conf` | 去掉每次请求的 TCP+TLS 握手 |
| `least_conn` + `proxy_next_upstream` 仅幂等 | 同上 | 502/504 不再打穿别的上游 |
| `proxy_cache orca_llm`（**只缓存 `GET /v1/models` 和明确的幂等 GET**）+ `proxy_cache_lock on` | 同上 | 多 agent 同时启动时，模型列表只打一次上游 |
| `/v1/models` 60s 缓存 + 上游响应的 `Cache-Control` 透传 | 同上 | 冷启动 QPS 峰值削平 |
| 客户端侧：system prompt 去时间戳、`prompt_cache_key` 固定为会话 id | 文档 + 模板 | 命中率 93% 是验收线 |

### P2 隔离与防封（半天）

| 动作 | 说明 |
|---|---|
| **验证 SG 机器的 IPv6 前缀** | `ip -6 addr` 有没有全局 /64。有 → §12.3 的零月费方案直接可用，本项 P2 整体降级为 P0 |
| 若 IPv6 不可用：3 台小 VPS（不同商家） | $9~15/月，每台背 3~4 个号，换真正不同 ASN。**优先级高于一切性能优化** |
| Go key ↔ 出口映射表落盘（`poolctl render` 打印成 `号 → 出口` 表） | 以后任何一次漂移都能一眼看出 |
| `check-opencode` 改成"从该号自己的出口发请求" | 现在是从宿主机出口发，测的不是生产路径 |
| Devin 每号出口隔离（待验证） | `auths/<id>.json` 能否带 `proxy-url` 未核实；已确认的是 CPA 有全局 `requests.proxy-url`（`config_v8.go:86`）。先拿一个号验证 |

### P3 观测与自动化（1–2 天）

| 动作 | 说明 |
|---|---|
| `poolctl quota` 增加第三列：每号近 5h/周/月真实消耗 + 预测撞墙时间 | 撞墙预测比"剩余百分比"有用 |
| 冷却梯度：429 → 15s → 5min → 到窗口 reset | 单级 15s 冷却等于每 15 秒再撞一次上游，是**主动喂风控** |
| 号池体检：`#pool`、重复支付方式标记、同 IP 号数 ≥2 告警 | 把 1.2 的坑变成会叫的告警 |

---

## 5. 容量账：10 Go + 5 Devin 到底够不够

用 `docs/capacity-planning.md` 的模型，形态取 **180k 上下文 / 1.5k 输出 / 93% 命中**（单请求 $6.58 milli），两人 300h/月：

| 并发形态 | req/h·人 | 月请求 | 月成本 | 需要 Go 号 | 10 号余量 |
|---|---|---|---|---|---|
| 正常深度 | 40 | 24,000 | $158 | 2 | 5× |
| 重度 3 并发 | 90 | 54,000 | $356 | 5 | 2× |
| 重度 4~5 并发 | 150 | 90,000 | $593 | 8 | **1.25×** |
| 极限 6~8 并发 | 250 | 150,000 | $988 | 13 | **不够** |

**结论：10 个 Go 在 4~5 并发下只剩 25% 余量；而且这个数字对命中率极度敏感**：

| 命中率 | 单请求 | 10 号月产能 | 90k 请求要几个号 |
|---|---|---|---|
| 97% | $4.47m | 179,000 请求 | 5.0 |
| 93% | $6.58m | 121,500 请求 | 8.0 |
| 85% | $14.2m | 56,300 请求 | 16.0 |
| 0% | $55.8m | 14,300 请求 | 63.0 |

命中山 93% 掉到 85%，10 个号从"富余"变成"差 6 个"。**任何让命中率掉 8 个点的设计改动，都等于把预算翻倍。**

Devin 侧不一样：免费池限流是**按号按请求作用域**的，5 个 Pro ≈ 5 路可用并发，与 Go 的额度逻辑完全无关。所以：

- Go 是**成本瓶颈**（撞月额度），扩号就是加钱；
- Devin 是**并发瓶颈**（撞 `Reached free model rate limit`），扩号就是加并发；
- 两人各跑 2~3 个常驻 agent（主 + 子 agent）时，5 个 Pro 刚好，**再加 2 个就稳**；10 个 Go 顶得住 4~5 并发，顶不住 6+。

---

## 6. 防封禁：清单要能被执行，不是口号

### 6.1 号级不变量（违反一条就可能连坐）

1. 一个号一个出口，出口是静态 IP，永不漂移。
2. 不同号不共用支付方式、不共用设备指纹、不做链路自邀。
3. 同一出站 IP 上不超过 2 个号。**当前违反**：10 个号 1 个出口。缓解方案见 §12.3（零月费的 IPv6 出口，或 3 台小 VPS，$9~15/月）。
4. UA / `x-opencode-session` 等标识由网关统一铸造，不随客户端变。
5. 别把一个号的凭据同时用于"生产流量 + 批量探测"。

### 6.2 工程保障（把不变量变成代码）

- `render` 拒绝 `#pool`：这是唯一一个"配一下就能让风控炸掉"的开关，必须硬失败而不是 warn。
- 打印并落盘 `号 → 出口` 映射表，P2 的验收物。
- `check-opencode` 走每号自己的出口：现在的实现只证明"这台机器能连上游"，不证明"这个号的出口能用"。
- **不要**用零宽混淆当防封手段。它是"降低关键词命中率"，README 自己也这么写；把它写进"防封禁"清单里会让人误判风险。

### 6.3 红线

- 号与号的订阅不能合并（Go 是单成员 workspace），所以"省一个号"永远只能靠缓存，不能靠共享。
- 5 个 Devin Pro 里任何一个进了冷却，都优先让**新会话**避开它，而不是把**老会话**踢走——老会话的缓存价值远高于一次 429。

---

## 7. 缓存命中工程

### 7.1 三层结构

```
客户端前缀 (L0)   →   ingress in-flight 合并 (L1)   →   上游 prefix cache (L2)
  system prompt         GET /v1/models 60s            Go: 5h 窗口内按前缀
  工具定义顺序           幂等 GET 同 key 串行          Devin: 按凭据粘会话
```

先说清楚一件事：**`/v1/chat/completions` 不能做响应缓存**。请求体里带 `stream`、会话历史、每次不同的消息；缓存命中率会趋近于零，只会白吃内存和磁盘。入口能缓存的是"元数据类幂等请求"（`/v1/models`、模型详情），以及在途合并（同一个 key 的在途请求只打一次上游）。断言任何"缓存 chat 响应"的方案，都是没算过命中率。

### 7.2 L1 设计（nginx）

```nginx
proxy_cache_path /tmp/orca-cache levels=1:2 keys_zone=orca_meta:16m
                 max_size=256m inactive=10m use_temp_path=off;
# key：方法 + host + uri（不带 Authorization）+ 模型参数
proxy_cache_key "$scheme$request_method$host$uri$is_args$args";
proxy_cache_lock on;          # 在途合并，防冷启动惊群
proxy_cache_lock_timeout 30s;
proxy_cache_valid 200 60s;
proxy_cache_use_stale updating error timeout;
```

只给 `location = /v1/models` 系上 `proxy_cache orca_meta`；chat 路径继续 `proxy_cache off`（现状已经是 off，保持）。

### 7.3 L2 设计（会话粘性）

| 跳 | 粘性字段 | 现状 | 目标 |
|---|---|---|---|
| CPA → Devin | `session-affinity` + `session-affinity-ttl` | 开，2h，子 agent 不继承 | 子 agent **继承**；`rebind-on: quota` |
| CPA → oc2api | `conversationSeed(系统提示+首轮)` | 天然成立 | 别做"改进"，做完反而漂 |
| oc2api → Go key | `CursorFor(session)` | 成立 | 保持；代理绑定跟着 key 走，不要动顺序 |

### 7.4 命中率失效源清单（一条一条排查）

1. system prompt 里有时间戳 / 随机 ID / 当前日期。
2. 工具定义顺序每次重排（JSON 序列化不保序）。
3. 同一会话被轮询到不同 key（亲和被关 / TTL 太短 / 子 agent 不继承）。
4. 客户端每次换 `prompt_cache_key`。
5. 5h 窗口切换导致上游真把缓存清了（不可控，只能观测）。
6. 上下文超过上游缓存块粒度后被切成不对齐的块。
7. 冷却把会话踢到新号，前缀在新号上是冷的（**这就是给"冷却要有梯度"的理由**）。

### 7.5 度量与验收

`cache_read / input_tokens ≥ 0.93`。低于这个数先别加号，先查 7.4。当前实测基线 95.53%（`docs/capacity-planning.md`）。

---

## 8. 高并发：把"能扛"拆成四件事

### 8.1 连接层

- `upstream` 块显式声明 + `keepalive 64`：省掉每请求的握手，是最便宜的一档提速。
- `least_conn`：长流（SSE 动辄几分钟）占连接不均，轮询会把新请求塞给已经在跑 5 条流的那台。
- **`proxy_http_version 1.1` + `Connection ""` 必须同时出现**，否则 keepalive 不生效——这两个现在在 `location` 里是写死的，改成 upstream 之后别丢。
- 若开 HTTP/2 上游：Go 的共享 `Transport` 在收到 `PROXY` 环境变量时会触发 `GODEBUG=http2client=0` 兼容分支（历史坑），容器里别设 `PROXY`。

### 8.2 池层

- `opencode2api.performance.max_idle_conns: 2048` 实际天花板是 `max_idle_conns_per_host(256) × host 数`。当前只有 1 个 host，2048 是空配置，**改成 512** 更诚实（或者干脆理解为 256）。
- `max_conns_per_host: 0`（无限）与服务端无关，只约束客户端侧。配合 `failure_cooldown_seconds: 15` 会形成"失败 → 冷却 15s → 立刻重试"的抖动，见 8.3。
- `attempt_timeout_seconds: 0` / `first_event_timeout_seconds: 0` 表示无限等待。上游挂住时会攒僵尸流，**建议 `first_event_timeout_seconds: 90`，`attempt_timeout_seconds: 600`**。

### 8.3 冷却层（现在最薄的一环）

现在的口径是"429 → 该号冷却 15s"。这在号多的时候等于每 15 秒再撞一次同一堵墙，上游看到的是持续的 429 风暴。改成梯度：

| 触发 | 冷却 | 依据 |
|---|---|---|
| 单次 429 | 15s | 瞬时抖动 |
| 5 分钟内第 2 次 | 5min | 窗口真的疼了 |
| 同 key 连续 3 次 | 到该 key 的 `usage.reset` | 只能等窗口 |
| 5xx / 超时 | 30s，不动会话绑定 | `rebind-on: quota` |

Devin 侧的 `request-scoped-errors` 已经是"换号继续 + 冷却"，要补的是 `500` 之外的 **402/403 传播**（现在只匹配 429/500 两个 status）。

### 8.4 会话压缩（per-server-key 桶）

如果两人 + 多 agent 在某个时刻并发超过 5 路 Devin 池，正确行为不是返回 429，而是**把第 6 个会话排进队列**，并且在队列里改走 Go 池（如果模型允许）。
这就需要一个"per server key 的会话桶 + 溢出路由"，落点在 oc2api（它已有 server key 维度：`server_key_metadata` + `/api/monitor` 的 `usage.server_keys`）。这是本设计里**唯一需要写 Go 代码**的地方，也是最划算的一处。

---

## 9. 代码改动清单（12 条，可直接拆 issue）

| # | 文件 | 现状 | 改成 | 优先级 |
|---|---|---|---|---|
| 1 | `scripts/poolctl.py` | `session-affinity-subagents: false` | `true` | P0 |
| 2 | `scripts/poolctl.py` | 无 `rebind-on` | `quota` | P0 |
| 3 | `scripts/poolctl.py` | `#pool` 只 warn | 硬失败（`PoolctlError`） | P0 |
| 4 | `scripts/poolctl.py` | `max-retry-credentials: 0` 语义未核 | 核实后写注释或调值 | P0 |
| 5 | `nginx/nginx.conf` | 无 upstream 块 | `upstream` + `keepalive 64` + `least_conn` | P1 |
| 6 | `nginx/nginx.conf` | 无缓存 | `orca_meta` zone + `/v1/models` 缓存 60s + `cache_lock` | P1 |
| 7 | `nginx/nginx.conf` | `proxy_next_upstream` 默认 | 只对幂等方法生效 | P1 |
| 8 | `scripts/poolctl.py` | 无出口映射输出 | render 打印/落盘 `号 → 出口` 表 | P1 |
| 9 | `scripts/poolctl.py` | `check-opencode` 从宿主机出口发 | 从该号自己的代理发 | P1 |
| 10 | `opencode2api/config.json` | `first_event_timeout: 0` | 90s / attempt 600s | P2 |
| 11 | `opencode2api/config.json` | `max_idle_conns: 2048` | 512（对齐 per-host 语义） | P2 |
| 12 | oc2api 源码 | 无会话桶 | per-server-key 会话桶 + 溢出路由 | P3 |

13 条附录：`poolctl quota` 增加"预测撞墙时间"，冷却梯度落进插件 `quota.fetch` 的返回结构。

---

## 10. 风险与已知空白

| 项 | 状态 | 处理 |
|---|---|---|
| Devin 每号独立出口 | **待验证**：`auths/<id>.json` 是否能带 `proxy-url`（优先级链见 `helps/proxy_helpers.go:39`） | 拿 1 个号试，成功再铺 5 个 |
| `max-retry-credentials` 语义 | **待核实** | 读上游 + 实测 |
| Go 的 5h/周窗口比例 | 官方 FAQ 说 20%/50%，2026 年改过两次 | 每月复核一次，别历史外推 |
| SWE-2 免费截止 | 官方两处文档冲突 | 按"可能收费"规划；一旦收费，SWE-2 立即退回 DeepSeek |
| `/usage` 不等于可消费余额 | 百分比、订阅级、不可相加 | 容量规划只能当估算，不能当账本 |
| CPA 单容器 | 无每凭据并发闸门 | 靠上游 429 + 8.3 梯度兜底；真要硬闸只能自研（方案 B 的代价） |

---

## 11. 验收

| 指标 | 目标 | 测量 |
|---|---|---|
| cache_read / input | ≥ 93% | CPA usage 统计（README §7） |
| 上游成功率 | ≥ 99% | CPA `usage-statistics` 的 2xx 比 |
| 429 后二次 429 间隔 | > 5min | 冷却梯度生效后日志里同 key 429 的间隔 |
| `#pool` 出现次数 | 0 | `grep -c "#pool" accounts.yaml` + render 硬失败 |
| 号 ↔ 出口映射稳定 | 7 天无变化 | P1.8 的映射表 diff |
| `/v1/models` 上游回源 | ≥ 60s 一次 | nginx `$upstream_cache_status` = HIT 比例 |
| 冷启动并发 20 会话 | 无 429、无超时 | 压测脚本（P1 之后补） |

---

## 12. v2 补章：单出口 + 同支付渠道下的降级设计

### 12.1 关联维度盘点

风控看到的是"关联"，不是"账号数"。现在有三条线：

| 维度 | 现状 | 上游可见性 | 能不能改 |
|---|---|---|---|
| 出站 IP | 10 Go + 5 Devin **同一个 IPv4** | 高（每个请求都带） | 能，见 §12.3 |
| 支付渠道 | Go 全支付宝、Devin 全同一张 HSBC HK 卡 | 高（实名/卡 BIN） | 半能，见 §12.5 |
| 时间节奏 | 同一天开号、同一时段活跃 | 中 | 能，零成本 |
| 设备/UA | 已由 oc2api 统一铸造 | 低 | 保持不动 |

**结论：支付这条线比 IP 更难改，而且更难解释。** 如果 10 个 Go 是同一个支付宝账户付的，那"同一实名人 10 个单成员 workspace"在上游眼里是确定的，IP 怎么拆都只是减分不减根。

### 12.2 1-3-1：出口方案

**问题**：在没有静态代理预算的前提下，让 10 个号在出站网络上互相不可关联。

- **A 直连 + 行为隔离**（$0）：承认 10 个号共用一个 IP，只做时间/行为/支付节奏的去同构。
  **优点**：零成本、零运维。**缺点**：IP 维度的关联完全敞开，一个号被按 IP 连坐，10 个一起走。
- **B 自建静态出口**（$0 ~ $15/月）：两种做法——
  - **B1 IPv6 源地址绑定**：SG 机器若有全局 /64，跑一个本地 SOCKS5（3proxy/dante），监听 10 个端口，每个端口 `external` 绑一个不同 IPv6；oc2api `proxies` 填 `socks5://127.0.0.1:1081..1090`，靠已验证的 `i % len(proxies)` 映射做一号一出口。**月费 0**。
  - **B2 三台小 VPS**（$3~5/月/台，不同商家）：每台背 3~4 个号。**真隔离**（不同 ASN），成本约为订阅总价的 6%。
  **优点**：不打乱现有架构，只加 `proxies` 条目。**缺点**：B1 的 10 个地址同属一个 /64，风控若按 /48 或 ASN 聚合仍会关联，是"降关联"不是"断关联"；B2 要花 $9~15/月。
- **C 住宅代理**（$50+/月）：**已否决**，成本高于收益，且住宅代理池的 IP 复用率本身就让"静态绑定"名不副实。

**推荐：先 B1、后 B2、A 兜底。** 顺序是先花半天验证 `ip -6 addr`，有 /64 就当天上 B1（零成本拿到 10 个出站地址）；同时把 B2 当成"每月 $12 买断连坐风险"的保险，一旦出现第一个 429/IP 封禁信号就立刻上。

> B1 的前提必须实测：SG 机器有没有可路由的 IPv6 前缀、上游接受不接受 IPv6 连接。两项任一不成立就退回 A + B2。

### 12.3 单出口下"高并发"的真实天花板

这是最容易被忽略的一条：**号数给的是额度，IP 给的是速率。**

- 额度维度：10 × $80/月，与 IP 无关；
- 速率维度：上游若按 IP 做限流/风控，10 个 key 共享的是 **1 份 IP 配额**，不是 10 份；
- 所以单出口时"堆号"换不来并发，只能换来"同一 IP 上更多 key 同时被限流"。

直接后果：§8.4 的 per-server-key 会话桶，阈值要按 **IP** 算而不是按号算；§8.3 的冷却梯度从"优化项"升级成**必需项**——现在 15s 单级冷却意味着同一个撞墙的 key 每 15 秒重撞一次，在单 IP 下等于主动向风控报数。

### 12.4 支付渠道怎么降关联

| 动作 | 理由 | 成本 |
|---|---|---|
| **逐个核实 10 个 Go 是不是同一个支付宝账户** | 同一支付宝付 10 个单成员 workspace = 最强关联指纹（比 IP 强，因为支付要实名） | 0，但要如实回答 |
| 错开月付续费日 | 同一商户同一天 10 笔同额扣款是典型批量开号特征 | 0 |
| 混合档位 | $10 与 $20 混订，避免"10 个完全同构的订阅" | 低 |
| 新号分批上线 | 每周最多 1 个，首月只跑最低档，满一个计费周期再升 | 0，只是慢 |
| Devin 侧**不要**再叠加额外不一致 | 卡是 HSBC HK、机器在新加坡、日常使用在大陆——地域不一致本身已是特征，再改 VPN/代理只会让登录链路更可疑 | 0 |

### 12.5 零成本的四件小事（不加代理也要做）

1. **冷却梯度**（§8.3）——单 IP 下这是止损的第一道闸。
2. **失败即静默**：一个 key 进入冷却后，不要让它再被选中，直到窗口 reset，而不是 15s 后再试。
3. **行为去同构**：不要让 10 个号在同一分钟打同一模型。oc2api 的 `CursorFor(session)` 已经按会话散开，够用，别关。
4. **`#pool` 硬失败**：这条在单出口下反而更重要——没有兜底 IP 时，任何轮换都意味着 IP 直接漂移。

### 12.6 对验收表的增补

| 指标 | 目标 |
|---|---|
| 出站地址去重数 | B1：10 个 IPv6；B2：3 台 VPS；A：1（已知缺陷，需书面接受） |
| 同 key 429 后的二次 429 间隔 | > 5min（现在约 15s） |
| 同一支付宝账户对应的 Go 号数 | ≤ 3（若为 1 个账户付 10 个号，需明确写下"接受此风险"） |

---

## 附：这份文档的自我说明

- "已核对"= 对上游源码（`opencode2api` / `CLIProxyAPI` 当前 main）逐行看过；"待验证"= 没看过实现，只看了配置文档。
- 没有引用任何未落地的第三方数字；容量表全部来自 `docs/capacity-planning.md` 的口径（$6.58m/单请求，93% 命中）。
- 本文不替代 README：README 讲"怎么部署"，本文讲"为什么这么设计、下一步改哪里"。