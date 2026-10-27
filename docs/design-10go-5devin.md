# 8–10 OpenCode Go + 5 Devin Pro：号池深度设计

> 设计稿 v1 · 基线 commit `4f43e73` · 所有"已核对"结论都指向上游源码行号，未核对的一律标注"待验证"。
> 目标读者：维护这个池的我们自己。读完应该能直接开工，不需要再问背景。
>
> **v2 补章（§12）**：生产环境暂无静态代理、10 个 Go 全走支付宝、5 个 Devin 全走同一张汇丰香港万事达卡。约束变了，§12 重算了风险模型与出口方案，§6/§9 的相关条目以 §12 为准。
>
> **v3 现状修正（§13）**：实际只上线 **4 个 Go**（3 个同源 + 1 个第三方），**Devin 尚未上线**；服务器确有全局 IPv6 `/64`。§13 把排期倒过来——**先铺出口，再扩号，Devin 最后上**。§12 里关于 10 个号的假设以 §13 为准。
>
> **v4 归因更正（§14）**：§13 对"4 key 只见 3"的解释（插件不响应配置变更）**被源码证伪**——配置确实推送，只是静默无日志。§14 给出真因、插件 5h 聚合的 bug 修复、`poolctl` 漂移检测，以及对本仓库上游的 PR。池视图保持**纯聚合口径**，不开逐 key 行（§14.2 说明理由）。

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

**推荐：先 B1、后 B2、A 兜底。** SG 机器已确认有全局 IPv6（`240d:c000:f020:1b00::/64`），B1 从"待验证方案"升级为**当天可上线**，施工单见 §13.3。B2 降级为"出现第一个 IP 级封禁信号时的保险"。

> 仍需实测的两件事：这个 `/64` 是不是整段在本机可用（而不是只放通那一个 `/128`）、上游接不接受 IPv6 建连。见 §13.3 第 1 步。

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

## 13. v3：现状修正与施工单

### 13.1 实际盘面

| 项 | v1/v2 假设 | 实际 |
|---|---|---|
| Go 号数 | 10 | **4**（3 个同源账户 + 1 个第三方账户） |
| 出站 | 静态代理 2~3 个 | **单 IPv4**，但**有可用 IPv6 `/64`** |
| Devin | 5 个 Pro 在线 | **未上线** |
| 支付 | Go 全支付宝、Devin 同卡 | 同上，且 Go 中 3 个出自同一支付宝 |

> 注："3 个同源"按"同一支付宝开立的 3 个订阅"理解。若实际是同一支付宝下的不同支付、或另有他人代付，需在 §13.4 的权重表上重算。

### 13.2 三条直接结论

1. **今天最该做的不是加号，是把这 4 个号拆到 4 个出站地址上。** 加号只会把"4 号 1 IP"变成"10 号 1 IP"，关联密度不降反升。
2. **第三方那个号是唯一的支付多样性资产。** 3 个同源号一旦按支付人连坐，只有它活下来。所以它要**单独出口、低权重、不当唯一路径**。
3. **Devin 排在最后是对的。** 5 个 Pro 上线的时机应该是"Go 侧出口已经拆开"之后——否则同一 IP 上同时跑 4 个 Go 和 5 个 Devin，等于在一个地址上叠两种被风控关注的流量。

### 13.3 IPv6 施工单（半天，$0）

**第 1 步：验证 `/64` 是否整段可用**（在 SG 服务器上跑）

```bash
ip -6 addr show scope global | grep 240d          # 确认前缀与网卡名（eth0 / ens3 / enp1s0）
sudo ip -6 addr add 240d:c000:f020:1b00::101/64 dev eth0   # 临时绑定，重启即失效
curl -6 --interface 240d:c000:f020:1b00::101 -s https://ifconfig.co
```

- 输出 `240d:c000:f020:1b00::101` → 整段可用，继续。
- 超时 / 输出仍是原地址 / `Cannot assign requested address` → 该 `/64` 只在运营商侧路由、未下发到本机。先查云厂商控制台的 IPv6 配置（Hetzner 需在面板启用 routed /64；部分商家只给 /124）。
- 验证完立刻 `ip -6 addr del ...` 撤掉，正式绑定放第 2 步。

**第 2 步：为每个号绑一个地址**

```bash
for i in 101 102 103 104; do
  sudo ip -6 addr add 240d:c000:f020:1b00::$i/64 dev eth0
done
ip -6 addr show dev eth0 | grep 240d
```

**第 3 步：每个地址开一个本地 SOCKS5 端口**（3proxy，一个进程、每个监听绑不同源地址）

```
# /etc/3proxy/3proxy.cfg
nserver 1.1.1.1
nserver 2606:4700:4700::1111
socks -p1081 -e240d:c000:f020:1b00::101
socks -p1082 -e240d:c000:f020:1b00::102
socks -p1083 -e240d:c000:f020:1b00::103
socks -p1084 -e240d:c000:f020:1b00::104
```

**第 4 步：逐端口验收**（不能省）

```bash
for p in 1081 1082 1083 1084; do
  echo -n "port $p -> "; curl -6 -s -x socks5h://127.0.0.1:$p https://ifconfig.co; echo
done
```

四个端口必须输出**四个不同地址**。任何一个回落到原地址，说明 `-e` 没生效或该地址不可出网。

**第 5 步：接进 oc2api**

```yaml
opencode_go_pool:
  proxies:
    - "socks5://127.0.0.1:1081"
    - "socks5://127.0.0.1:1082"
    - "socks5://127.0.0.1:1083"
    - "socks5://127.0.0.1:1084"
```

已核对：oc2api 按 `i % len(proxies)` 确定性绑定（`internal/gateway/pool.go:637-643`），所以**列表顺序就是"第 i 个 key → 第 i 个出口"**。顺序一旦上线就不要再重排，重排等于让所有 key 换 IP。

> 容器里跑 3proxy 需要 `network_mode: host`（要绑宿主机网卡上的 IPv6）。更简的做法：3proxy 装在宿主机，oc2api 容器经 `host.docker.internal` 或网桥网关访问 `1081~1084`。

### 13.4 四号池的权重与用法

| 号 | 来源 | 出口 | 权重 | 理由 |
|---|---|---|---|---|
| go-1 | 同源 | `::101` | 1 | 主力 |
| go-2 | 同源 | `::102` | 1 | 主力 |
| go-3 | 同源 | `::103` | 1 | 主力 |
| go-4 | 第三方 | `::104` | **0.5~1** | 唯一的支付多样性资产，压低负载当保险；**不要**让它成为某模型的唯一提供者 |

单 IP 时代的"堆号换并发"在这里依然不成立（§12.3）：4 个号现在有 4 份额度但只有 1 份 IP 速率；拆开之后才变成 4 份/4 份——这才是"高并发"真正落地的那一步。

### 13.5 排期（与 v1 的 P0~P3 对齐）

| 序 | 动作 | 为什么排这里 | 工期 |
|---|---|---|---|
| 1 | §13.3 的 IPv6 五步 | 后面每一步都建立在"号已分开"之上 | 半天 |
| 2 | 冷却梯度（§8.3） | 单 IP 下 15s 冷却 = 每 15 秒重撞一次，等于主动报数 | 半天 |
| 3 | `#pool` 硬失败 + 号→出口映射落盘 | 没有兜底 IP 时，轮换=直接漂移 | 1h |
| 4 | 4 → 6 扩号（错开续费日、混合档位） | 拆完出口再扩，密度才不回升 | 按开号节奏 |
| 5 | Devin 上 2 个 Pro，跑两周 | 先量出真实"条/天"再决定加到 5 个 | 2 周观察 |
| 6 | 6 → 10 扩号 + Devin 补到 5 个 | 视第 5 步实测结论定档 | — |

### 13.6 Devin 上线前的两个前置

1. **给 Devin 留不同的地址族**：Go 走 IPv6 `::101~104`，Devin 走服务器原生 IPv4。不同地址族在风控侧是不同桶，等于白拿一份隔离。
2. **别把 Devin 的登录和扩号挤在同一天**：OAuth 首次登录会留设备/网络指纹，同 IP 同一天连登 5 个 Devin 账号是典型批量特征。


---

## 14. v4：插件可观测性与"3 key 假象"的最终归因

§13 里我判断"4 个 key 只有 3 个生效"，给出的解释是 **CPA 的 C ABI 插件不响应配置变更**。这条结论**已被源码证伪**，记录下来免得以后重复踩。

### 14.1 真相：配置会被推送，但推送不可观测

把上游 v8.0.43 到 main 的插件链路逐段读完，事实是：

| 环节 | 位置 | 行为 |
| --- | --- | --- |
| 配置写入 → 运行时刷新 | `internal/watcher/config_reload.go:157` → `sdk/cliproxy/service_config.go:215` | 文件变更经 fsnotify 重载后，**会**走 `syncPluginRuntimeConfigForConfig` → `host.ApplyConfig` |
| 已加载插件重注册 | `internal/pluginhost/host.go:365-374` | `registeredNow=false` 时调 `callRegister` |
| 方法选择 | `internal/pluginhost/host.go:1116-1122` | 已注册 → `pluginabi.MethodPluginReconfigure`，`ConfigYAML` 随包下发 |

所以配置**确实**推给了运行中的 `.so`。真正的坑是**没有任何日志**：只有首次加载会打 `pluginhost: plugin loaded` / `plugin registered`，之后的 `reconfigure` 是一条静默 RPC。插件要么重读、要么忽略，从日志上完全看不出区别——这才是"面板显示 3 个 key"能存在 10 小时不被发现的原因。

> 教训：`plugin.reconfigure` 存在 ≠ 它被调用 ≠ 插件处理了它。三者只能靠日志区分。

### 14.2 设计取舍：池视图只给聚合，不开逐 key 行

一度实现过"每个 key 一行 + 行标题标最紧窗口"，后来按预期设计撤掉了：**池视图的语义就是"这些 key 合起来还剩多少额度"**，逐 key 明细是另一层信息，塞进同一个视图会让"综合剩余"这件事失焦。

真正的设计意图是：**所有 OpenCode Go key 的综合剩余用量**是唯一对外口径。所以插件侧决定不动，保持上游形态：

- 池视图只有一行 `所有 key 综合`，每个窗口（5h / 周 / 月）给的是健康 key 的均值，描述里带 `（<n> key 均值）`，n 就是参与聚合的 key 数。
- 失败的 key 仍单独成行（`query failed: ...`）：那不是"用量"，是故障，必须可见。
- 明细要看单个 key 的额度时，走 `opencode-go-N.json` 那张单凭据，插件本来就有这条路径（`singleKey`），不必把明细塞进池视图。

（更正一处我此前的误判：`agg` 表的初始键是 `rolling/weekly/monthly`，而循环里写的是 `agg["5h"]`；我曾据此认为 5h 均值一直取到零值。实际上 `a := agg[w.name]` 对缺失键取零值就是新建，循环会把 `"5h"` 键正常建出来——**没有这个 bug**，两处命名不一致只是可读性问题。）

约束记一笔：host 端 `QuotaMetric.Value` 是 `float64`，任何字符串型 summary 指标都会让整个响应解码失败（`json: cannot unmarshal string into ... value of type float64`）。**summary 只能放数字**，文字信息只能进 `displayName` / `description`。

线上验收（2026-10-27 15:25 UTC，空窗重启后，逐 key 版）——顺带记录当时的分布，说明为什么"聚合 + Failing"够用：

```
Accounts 4 | Failing 0 | Min remaining 67%
5h 均值 100% · 周 98% · 月 92%
```

四个 key 里三个的月度都在 99~100%，唯一吃紧的第四个是月度 67%（reset 2026-11-18）。也就是说**聚合视图上没有异常信号，`min_remaining 67%` 只反映那一个号**；这与"池子整体健康"并不矛盾，也是保留聚合口径的前提——真出现多号同时下滑时，均值会立刻掉下来。

### 14.3 `poolctl render` 检测插件配置漂移（`scripts/poolctl.py`）

- 每次 render 后把 `plugins.configs` 里受管插件配置的哈希与"当前进程启动时间"一起写进 `.poolctl-plugin-state.json`。
- 下次 render 时若**同一进程**（启动时间未变）却出现了不同的插件配置，就告警"配置已变更但进程未重载"，并提示 `docker compose restart cliproxyapi`。
- 两个条件缺一不可：只看哈希会在"停机期间写过配置、随后启动"时误报；只看启动时间则根本抓不到。报警时不回写状态，所以重启前每次 render 都会继续提醒。
- CPA 没跑时（纯本地仓库）不告警，只记账。8 种场景（首跑/未变/新增 key/重复提醒/重启后/停机写/重启加载/再次新增）已逐一验证。

### 14.4 上游 PR（指本仓库的上游，不是 CPA）

- 仓库上游是 [AzureLandin/orca-ai-pool](https://github.com/AzureLandin/orca-ai-pool)（master，本人只有 READ 权限），本地 `upstream` remote 指向它，`origin` 是自己的 fork。
- 提请内容：`scripts/poolctl.py` 的插件配置漂移检测（§14.3）。理由——插件配置写盘后是否需要重启 CPA，是个只有 render 一侧才知道的事实；让 render 直接告警，比事后翻日志找"为什么面板少了 key"便宜得多。
- 另附一条给 CLIProxyAPI 的独立 PR：[router-for-me/CLIProxyAPI#7050](https://github.com/router-for-me/CLIProxyAPI/pull/7050)，给静默的 `plugin.reconfigure` 补一条日志。它解释的是同一个现象（配置推了但看不见），只是修在上游更合适。

### 14.5 仍未闭环

1. **CPA 版本落后一档**：线上跑 `v8.0.43`，上游已发 `v8.0.44`。§13 的空窗重启流程可复用，升级前先确认 44 版没有插件 ABI 变更。
2. **`management.secret-key = sk-123456` 仍未轮换**，且 8317 直接暴露公网（§12 已列为 P0）。
3. **聚合均值的"预警阈值"还没定**：现在只报 `min_remaining`，没有"综合均值跌破多少该扩号"的规则；建议结合 §13.5 的扩号节奏一起定。

---

## 附：这份文档的自我说明

- "已核对"= 对上游源码（`opencode2api` / `CLIProxyAPI` 当前 main）逐行看过；"待验证"= 没看过实现，只看了配置文档。
- 没有引用任何未落地的第三方数字；容量表全部来自 `docs/capacity-planning.md` 的口径（$6.58m/单请求，93% 命中）。
- 本文不替代 README：README 讲"怎么部署"，本文讲"为什么这么设计、下一步改哪里"。