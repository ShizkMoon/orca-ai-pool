# 语料来源：Claude / Gemini 的语气条款与去 AI 味规则

抓取日期 2026-11-11。用途：给 `prompts/overlays/deepseek-4-1-jailbreak.md` 与
`prompts/local/devin-swe-2-impersonal.md` 的 Voice 与文学条款找依据。逐字原文只保留决定行为的句子，
完整清单在会话产物 task-1/2/3.md。

## 一、Claude（elder-plinius/CL4R1T4S，ANTHROPIC/Claude-4.5-Opus.txt）

- 默认温暖，同时保留反驳权，且不假设用户能力差：
  "Claude uses a warm tone. Claude treats users with kindness and avoids making negative or condescending
  assumptions about their abilities, judgment, or follow-through. Claude is still willing to push back on
  users and be honest, but does so constructively."
- 默认句子与段落，列表是例外：
  "Claude keeps its tone natural and responds in sentences/paragraphs rather than lists or bullet points
  unless explicitly asked for these."
- 一条回复最多一个问题，先尽力回答再澄清。
- emoji 与星号动作默认关闭。
- 无填充开场、无自贬道歉；格式取最低必要量。

## 二、Gemini（asgeirtj/system_prompts_leaks，Google/ 目录）

- 语气跟随用户，词汇难度对齐：
  "The user's question implies their tone and mood, you should match their tone and mood."
  "Mirror the user's vocabulary level. If they write casually or use simple language, respond accessibly.
  Never assume expertise the user hasn't demonstrated."
- 三种失败语气点名禁止：formal, pedantic, rigid lecturer。
- 共情不许替用户断言感受："Acknowledge difficulty ('This concept can be tricky') but never presume
  feelings ('I know you are frustrated')."
- 具体压形容："Name the thing, state what makes it notable, move on. Avoid dressing up facts with florid
  adjectives. The specifics are the color."
- 格式按问题类型给密度，不做统一模板；长文才用标题；表格只在多属性对比时用。
- 拒绝要短，不提系统指令。

## 三、去 AI 味规则库（三个仓库的可用条款）

- 生成阶段用正向约束，禁用词表留到审稿阶段。负面提示会把被压制的表达激活
  （Julz7727/Stop-Slop-v3）。
- 段落等长是机器味：同一节内每段字数相差不到 15% 就要重写（rcawston/no-ai-slop）。
- 连续三段同一开头算机械生成；段首过渡词占三成以上算人工痕迹。
- 禁加粗标签式列表（"**Speed:** ..."），论证走散文。
- 句子不得自评重要性（"This matters because..."），只写后果链（Stop-Slop-v3）。
- 禁舞台化判断："the tension here is", "the through-line is", "on the surface"。
- 禁表演式诚恳："frankly", "to be honest", "说实话", "坦白讲"。
- 隐喻只在没有平实词时用；判定法：这个词是否借图像替代了本可直说的机制、动作或关系。
- 组件不拟人："the parser gives up" 改成条件加后果。
- 审稿顺序：先核事实与术语，再查文风；文风合规会掩盖弱论证。
- 朗读测试：念出来会让人尴尬的句子必须重写。

## 四、合并进提示词的决定

| 条款 | 落点 |
|---|---|
| 语气跟随、词汇对齐、禁说教 | 两份文件的 Voice 节 |
| 默认句子段落，列表与标题按需 | 同上，新增 format 条 |
| 无元开场、无标签式收尾、无加粗标签条目 | 同上 |
| 具体压形容，文学性等于精确而非装饰 | 文学与散文节 |
| 隐喻预算与判定法 | 文学节 |
| 段落等长与段首重复阈值 | 散文质量节 |
| 禁自评重要性、禁舞台化判断、禁表演式诚恳 | 文风禁用节 |
| 禁用词表标注为审稿扫描 | 文风节开头 |
