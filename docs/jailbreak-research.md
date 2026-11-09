# 破甲提示词社区研究

> 调研时间：2026-10。范围：SillyTavern（酒馆）官方文档与源码、公开破甲提示词仓库、
> 公开学术论文与基准。本文只分析提示词结构和工程含义，不复刻大段社区提示词正文。

## 1. 结论先行

1. 酒馆社区的主流做法不是“一个万能破甲提示词”，而是把提示词拆成多个槽位：
   Main Prompt、Auxiliary Prompt、Post-History Instructions、Author's Note、World Info
   和角色卡。每个槽位有自己的位置、角色和优先级。
2. 在 SillyTavern 的 Chat Completion 默认预设里，`nsfw` 对应 **Auxiliary Prompt**，
   `jailbreak` 对应 **Post-History Instructions**。也就是说，社区语境里的
   “jailbreak” 很多时候指的是“放在对话历史之后、优先级最高的那段补充指令”，
   而不是一个单独的安全绕过阶段。
3. 越靠近上下文末尾的注入，对下一次回复的影响越大。Author's Note 的 depth 0
   就是“放在最后一条消息之后”；Post-History Instructions 通常也是最后发送。
4. NSFW 在社区里通常按“内容层”处理：强度、风格、关系/ consent、节奏、视角分别配置，
   而不是把所有要求塞进主提示词。大型预设（如 Nemo Engine）把 NSFW core、intensity、
   style、consent 拆成独立模块。
5. 模型级去审查（abliteration / Heretic）和提示词级破甲是两条不同的路线。前者改权重，
   对 API 不可用；后者改上下文，能用于 API，但效果受模型和提示词质量影响。
6. 历史 DAN 类提示词主要利用角色扮演、双回答、token 预算和“保持角色”来压制拒绝。
   它们是重要样本，但不能当成现代模型的通用解。
7. 自动化研究（AutoDAN、GPTFuzz、EasyJailbreak）说明提示词可以通过改写、翻译、混淆、
   编码、拆分和迭代搜索来优化。这些方法更适合做评测和红队，不适合直接作为线上固定提示词。
8. 评估不能只看“是否越狱成功”，还要看格式合规、角色一致性、任务完成率和过度拒绝。
   XSTest 这类基准专门测量“把安全请求误判为不安全”的过度拒绝。

## 2. SillyTavern 的提示词结构

### 2.1 上下文由哪些部分组成

SillyTavern 官方文档把一次请求的上下文拆成：

- 主指令（Main Prompt / System Prompt）
- 角色定义、用户 persona
- World Info / lorebook
- Data Bank、历史摘要、网页搜索结果
- 聊天历史
- 用户当前消息
- 最后的指令（Post-History Instructions）

来源：<https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/index.md>

这和我们当前插件的 `system` / `instructions` / `messages` 三面模型相比，多出了
“对话历史之后的最后指令”和“按深度插入聊天历史”两个位置。

### 2.2 Post-History Instructions 的优先级

官方文档明确说：

- Post-History Instructions 在用户消息之后发送，是模型生成回复前收到的最后指令。
- 模型通常给它比 Main Prompt 更高的优先级，它可以覆盖 Main Prompt 的指令。

来源：同上，`Post-History Instructions` 一节。

这解释了为什么酒馆社区把 `jailbreak` 槽位放在最后：它不是“更强的系统提示词”，
而是“最后一句话”。

### 2.3 Author's Note 的深度

Author's Note 可以插到聊天历史的指定深度：

- depth 0：放在聊天历史的最末尾。
- depth 4：放在最近 3 条历史消息之前，成为历史里的第 4 个实体。
- “越靠近 prompt 底部，对下一次回复的影响越大。”

常见用途包括：提醒回复格式、强化指令、临时 World Info、角色 bias。

来源：<https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Characters/Author%27s-Note.md>

角色卡里也有等价的 Character's Note：按 @ Depth 插入，角色可选 User / System / Assistant。

来源：<https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Characters/characterdesign.md>

### 2.4 Prompt Manager 的顺序

Prompt Manager 是拖拽列表：

- 越靠上的提示词越早发送。
- 列表底部是最后发送的内容，通常就是 Post-History Instructions。
- `Position: In-Chat` 时可以指定 depth，忽略拖拽顺序。
- 同一 Role 和 Depth 的提示词按 Order 排序，顺序是 User、AI Assistant、System。

来源：<https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/prompt-manager.md>

### 2.5 默认预设里的 `nsfw` 和 `jailbreak`

SillyTavern 默认 Chat Completion 预设里：

```json
{
  "name": "Auxiliary Prompt",
  "identifier": "nsfw",
  "role": "system"
}
```

以及：

```json
{
  "name": "Post-History Instructions",
  "identifier": "jailbreak",
  "role": "system"
}
```

来源：<https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/openai/Default.json>

`PromptManager.js` 也把 `main`、`nsfw`、`jailbreak`、`enhanceDefinitions` 列为 system prompts，
其中 `main` 和 `jailbreak` 可以被角色卡覆盖。

来源：<https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/PromptManager.js>

这说明两件事：

1. NSFW 是独立于主提示词的辅助层，不是“把主提示词改得更色”。
2. Jailbreak 在 Chat Completion 里是最后指令槽位，适合放高优先级、短而硬的约束。

### 2.6 官方预设也鼓励“显式、graphic”的描写

官方 `Roleplay - Detailed` 系统提示词要求：

> Describe all actions in full, elaborate, explicit, graphic, and vivid detail.
> Mention all relevant sensory perceptions.

来源：<https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/sysprompt/Roleplay%20-%20Detailed.json>

这说明在酒馆的语境里，“explicit / graphic”本身就是一个合法的小说风格参数，
不应该和“安全绕过”混为一谈。对我们的设计来说，NSFW 是一个内容域，
不是把所有请求都当成安全攻击。

## 3. 社区破甲提示词的常见结构

### 3.1 DAN：角色扮演 + 双回答 + token 预算

DAN 仓库的 README 直接说这些提示词“exploit the role play training model”。
典型结构包括：

- 让模型扮演一个不受原规则约束的新身份。
- 要求同时输出“普通回答”和“破甲回答”。
- 用 token 预算、死亡威胁或“保持角色”来维持状态。
- 明确要求不拒绝、不加警告、不审查。

来源：<https://github.com/0xk1h0/ChatGPT_DAN>

DAN 是历史样本。它的价值在于展示了“角色框架 + 持续状态 + 输出契约”的组合，
而不是提供可直接复用的现代提示词。

### 3.2 In-the-wild 数据集：破甲提示词是持续演化的

CCS 2024 的 “Do Anything Now” 研究收集了 15,140 条公开提示词，其中 1,405 条被判定为
jailbreak prompts，来源包括 Reddit、Discord、网站和开源数据集。评估场景覆盖 13 类
禁止内容，其中包括 pornography。

来源：<https://github.com/verazuo/jailbreak_llms>，论文 arXiv:2308.03825。

对我们的含义：

- NSFW 是公开破甲提示词里的一个主要类别，不是边缘用例。
- 社区提示词会随模型更新快速演化，固定一份“万能提示词”很快会过期。
- 设计要支持多 profile、版本化和快速替换，而不是只维护一段文本。

### 3.3 自动化破甲：改写、翻译、混淆、编码、拆分

EasyJailbreak 把破甲流程拆成初始化、选择、约束、变异、攻击、评估，并实现了多种
公开 recipe，包括 ReNeLLM、GPTFuzz、AutoDAN、PAIR、JailBroken、Cipher、
DeepInception、MultiLingual、GCG、TAP、CodeChameleon。常见 mutation 包括：

- 改写（Rephrase）
- 翻译（Translate）
- 插入无意义字符、拼写错误、同义词替换
- Base64、ROT13、Leetspeak、Morse、ASCII 等编码
- payload splitting、二叉树/长度/倒序等结构化变形

来源：<https://github.com/EasyJailbreak/EasyJailbreak>，论文 arXiv:2403.12171。

AutoDAN 用分层遗传算法生成语义可读、可迁移的破甲提示词。

来源：<https://github.com/SheltonLiu-N/AutoDAN>，论文 arXiv:2310.04451。

GPTFuzz 用蒙特卡洛树搜索和变异生成破甲模板。

来源：<https://github.com/hubertyoo/GPTFuzz>，论文 arXiv:2309.10253。

这些方法的共同结论是：提示词不是一次性文本，而是一个可以搜索、变异和评估的对象。
线上系统不应自动变异提示词，但应该把提示词版本、评测集和替换流程设计好。

### 3.4 浅层安全对齐

“Safety Alignment Should Be Made More Than Just a Few Tokens Deep” 指出，当前安全对齐
往往只影响生成的前几个 token；通过 prefill、改写开头或把指令放到更靠近生成位置的地方，
可以显著改变模型行为。

来源：<https://github.com/Unispac/shallow-vs-deep-alignment>，论文 arXiv:2406.05946。

这和酒馆的 Post-History Instructions / Author's Note depth 0 是同一类观察：
**位置比篇幅更重要**。把同一段提示词放在系统开头，和放在最后一条消息之后，
效果可能完全不同。

## 4. NSFW 场景的社区实践

### 4.1 两条路线：改模型 vs 改上下文

模型级：

- Abliteration 通过方向消融移除拒绝方向。
- Heretic 自动搜索消融参数，同时最小化拒绝率和与原模型的 KL 散度。
- 它改的是权重，适合本地开源模型，不适合 API。

来源：<https://github.com/p-e-w/heretic>，论文 arXiv:2406.11717。

上下文级：

- 系统提示词、上下文污染、角色扮演、催眠、Post-History Instructions。
- 可用于 API 和线上模型；效果取决于模型、提示词和上下文。

一份中文社区经验总结把两者做了直接对比：模型修改更彻底，但系统提示词、上下文污染、
催眠等方法更通用，而且可以对 API 或线上模型使用；API 通常只有模型本身的安全限制，
没有网页端额外的二级审核，但 API 的模型质量和量化级别不一定透明。

来源：<https://github.com/CCSSNE/experience-of-Uncensored-LLM>

### 4.2 NSFW 是分层配置，不是一段大提示词

Nemo Engine 是一个公开的大型酒馆预设生态，把角色扮演拆成多个可组合层：
叙述声音、文风、节奏、视角、难度、世界逻辑、规划、追踪器、题材，以及可选的
NSFW 层。仓库里可以看到独立的 NSFW core、intensity、style、consent、scene duration
等模块。

来源：<https://github.com/NemoVonNirgend/NemoEngine>

这和我们当前“一个 profile 一段 prompt”的模型相比，最大的差别是：

- NSFW 强度是一个独立维度，不是布尔开关。
- 文风（浪漫、写实、色情、同人）是独立维度。
- consent / 关系框架是独立维度。
- 节奏、场景时长、视角也是独立维度。

如果把这些全部塞进一段 prompt，会出现互相冲突、难以调试、无法按场景替换的问题。

### 4.3 NSFW 提示词的工程结构

综合酒馆文档和社区预设，一个可维护的 NSFW profile 至少应分成：

| 层 | 作用 | 放在哪里 |
|---|---|---|
| 共享核心 | 优先级、冲突裁决、输出纪律 | system 开头或 post-history |
| 内容域声明 | 成人虚构、角色均为成年人、非真实人物 | system 开头 |
| 强度 | 暗示 / 感官 / 显式 / 极端 | 独立层，可切换 |
| 文风 | 浪漫、写实、类型文学、同人 | 独立层，可切换 |
| 关系/consent 框架 | 自愿、暧昧、权力差、非自愿虚构 | 独立层，可切换 |
| 节奏 | 慢热、场景时长、推进速度 | post-history 或 Author's Note |
| 输出契约 | 视角、时态、长度、格式、是否用括号写心理 | post-history |

### 4.4 位置建议

- 共享核心和成人虚构声明：system 开头，保证全局生效。
- 强度、文风、consent：可以作为独立 system 段，或合并进 NSFW overlay。
- 节奏和输出契约：post-history / depth 0，因为它们最影响“下一段怎么写”。
- 角色一致性：角色卡和 World Info 已经承载，不要在破甲提示词里重复覆盖。

## 5. 过度拒绝与评测

XSTest 是专门测量“夸张安全行为”的测试集：安全请求应该被满足，不安全的 contrast
请求才应该被拒绝。它提供了 refusal / compliance 的分类方法和完整提示词集。

来源：<https://github.com/paul-rottger/xstest>，NAACL 2024。

对 NSFW 设计的含义：

- 不能只用“是否拒绝”衡量成功，否则会把正常的成人虚构也当成失败。
- 要分别测：该拒绝的拒绝、该生成的生成、该露骨的露骨、该克制的克制。
- 过度拒绝会破坏角色扮演和创作体验，和破甲失败一样是质量问题。

建议的评测维度：

| 维度 | 说明 |
|---|---|
| 任务完成率 | 是否按请求写完场景，而不是中途停笔 |
| 拒绝率 | 硬拒绝、软拒绝、部分拒绝分别统计 |
| 显式度 | 是否达到 profile 设定的强度，而不是自动降级 |
| 角色一致性 | 语气、行为、关系是否连贯 |
| 格式合规 | 视角、时态、长度、JSON/工具调用是否有效 |
| 过度拒绝 | 对安全的成人创作请求是否误拒 |
| 缓存影响 | 提示词改动后前缀是否稳定，是否造成冷启动 |

## 6. 对我们设计的直接结论

1. **增加 `post_history` 注入位置。** 这是酒馆 `jailbreak` 槽位的等价物，也是 NSFW
   和角色扮演最有效的位置。它同时比 prepend 更缓存友好，因为前缀不变。
2. **增加 `in_chat` / depth 注入。** 对应 Author's Note；depth 0 放在最后一条消息之后。
3. **通用破甲和 NSFW 是两个内容域。** 不要共用一个 profile；用独立 overlay、独立强度、
   独立评测集。
4. **支持同一基座模型的多个变体。** 通用破甲和 NSFW 需要不同的别名后缀，例如
   `-jailbreak` 和 `-nsfw`，而不能只靠一个全局后缀。
5. **提示词分层，不写 mega-prompt。** 共享核心 + 内容域 + 强度/文风 + 模型 delta。
6. **把位置当作一等参数。** 同样的文本，放在 system 开头、最后一条消息之后、或
   depth 0，效果不同；manifest 要能表达位置，而不只是 prepend/append/replace。
7. **评测要包含过度拒绝。** 用 XSTest 类思路区分“该拒的拒了”和“不该拒的也拒了”。
8. **模型级去审查是另一条产品线。** 如果目标是本地模型，abliteration/Heretic 比提示词
   更彻底；如果目标是 API 号池，提示词和上下文才是主战场。

## 7. 来源清单

### 官方文档与源码

- SillyTavern Prompts: <https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/index.md>
- SillyTavern Prompt Manager: <https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/prompt-manager.md>
- SillyTavern Author's Note: <https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Characters/Author%27s-Note.md>
- SillyTavern Character Design: <https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Characters/characterdesign.md>
- SillyTavern Advanced Formatting: <https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/advancedformatting.md>
- SillyTavern default Chat Completion preset: <https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/openai/Default.json>
- SillyTavern PromptManager.js: <https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/PromptManager.js>
- SillyTavern Roleplay - Detailed preset: <https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/sysprompt/Roleplay%20-%20Detailed.json>

### 社区提示词与预设

- DAN: <https://github.com/0xk1h0/ChatGPT_DAN>
- Nemo Engine: <https://github.com/NemoVonNirgend/NemoEngine>
- 中文未审查模型经验: <https://github.com/CCSSNE/experience-of-Uncensored-LLM>
- In-the-wild jailbreak prompts: <https://github.com/verazuo/jailbreak_llms>

### 学术与工具

- AutoDAN: <https://github.com/SheltonLiu-N/AutoDAN>，arXiv:2310.04451
- GPTFuzz: <https://github.com/hubertyoo/GPTFuzz>，arXiv:2309.10253
- EasyJailbreak: <https://github.com/EasyJailbreak/EasyJailbreak>，arXiv:2403.12171
- Heretic / abliteration: <https://github.com/p-e-w/heretic>，arXiv:2406.11717
- Shallow safety alignment: <https://github.com/Unispac/shallow-vs-deep-alignment>，arXiv:2406.05946
- XSTest: <https://github.com/paul-rottger/xstest>，NAACL 2024
