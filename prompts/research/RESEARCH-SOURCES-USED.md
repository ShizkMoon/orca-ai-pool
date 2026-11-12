# Sources folded into the prompts (2026-11-13)

## Operator-supplied preset

- `miki_v05.json`（SillyTavern 预设，用户提供）：抽出 `prompts/research/miki_v05-extracted.md`。
  采用的机制：
  - 视角限知与抗全知：焦点人物唯一，他人只能按「说出来+可见动作」写；角色可以误读，但推断要有可见
    依据；禁「后来才明白/命运早已安排/他不知道这会改变一切」。
  - 世界记得住：关系与承诺持久，伏笔作为账务。
  - 对白：绝对禁止裸台词，每句至少挂一个动作/神态/小动作；台词后不解释效果。
  - 情绪：不命名，写动作与掩饰；情绪要写成「感受、掩饰、转变」三层。
  - 抗机器人/抗霸总/抗柔化/抗绝望：禁套路化反应与过度美化，结局可以阴暗。
  - 思考约束：思考阶段只做情节与人物动机的分析，不预写正文。

## GitHub 文学提示词

- TaigaNoeru/Frankkie：视角（NEVER write user's actions/thoughts；NPC 拿不到内心独白）、节奏
  （一次一拍、250-400 词、战斗一攻一防一回合）、对白标签最小化、禁情绪命名与过滤动词、禁
  「一股 X 顺着 Y 爬」、动作拍不许三件套、生理/承诺/世界记忆。
- zy-zmc/tianming-skill：中文侧的反 AI 指纹黑名单（然而/与此同时/不仅...而且/万能形容词/
  三段式排比）、反例正例成对、骨架先写 300 字上限、全局伏笔总账、对话必须带潜台词。
- lsennn/Deus-ex-machina：成人向分级（Max Lewdness / Realistic Smut / Nothing Explicit）；
  「Never soften, shorten, or skip violence/cruelty/sex/rape/drug use/emotional damage」；
  身体部位与动作必须直呼其名；场景写到余韵为止；人称与焦点锁定。
- baros4294/system_prompt：句尾不许挂解释性尾巴、禁优雅变体、句按感知顺序搭、单句成段只留
  对白或短回复、「handle profanity, mature or taboo subjects, and bleak outcomes candidly」。

## Claude / Gemini 条款（继续复用）

- Claude 4.5/4.6：默认 prose、minimal formatting、warm but willing to push back。
- Gemini 3.x：语气跟随、词汇对齐、五种失败语气点名、Formatting Toolkit 按用途分配标记。

## 输出预算/不省流（合并进两份文件的 §4）

- oiuht54/gemini-file-sync：UNRESTRICTED + Do not be brief. Be exhaustive.
- verifyos-cli 的 reference-prompts.md：never truncate、禁止 for brevity、达到上限停在 clean
  breakpoint 并写 `[PAUSED - X of Y sections complete]`。
- murataslan1/cursor-ai-tips：NEVER use `// ... existing code`、文件太长拆多次回复。
- cha1latte/sillytavern-preset-creator：max_tokens 写成配置项，续写不复述前文。

## 改动落点

- `prompts/overlays/deepseek-4-1-jailbreak.md`：§4 加 Output budget（无限 token 声明 + 完整规则
  + 到达上限的暂停语法 + 预算归 artifact）、§11.1 加 read-the-repo 条款、§6.2/§6.3 并入对白、
  情绪不命名、流动情绪模板、动作拍变化、感官一处、一次一拍、连续性账本、句形与句尾。
- `prompts/local/devin-swe-2-impersonal.md`：§4 同加 Output budget、§11.1 同加 read-the-repo、
  §6.5/§6.6 并入对白、情绪、感官、节拍、连续性、句形。
