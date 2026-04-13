# Auto-Transcribe 短视频脚本

> 目标时长：90-120 秒 | 适合：抖音 / 小红书 / Threads
> 风格：技术类 KOL 项目展示，快节奏，屏幕录制 + 真人出镜混剪

---

## 镜头 1 — Hook（0-8 秒）

**【画面】** 快速闪切 3 个痛点场景：
1. 手机播完一个 YouTube 视频，划走
2. 开完会，语音备忘录里一长条录音
3. Obsidian 空空如也

**【字幕】**
> 看完视频、开完会、听完播客
> 笔记在哪？

**【旁白】**
"你有没有这种感觉 — 看了很多、听了很多，但什么都没留下来？"

---

## 镜头 2 — 核心卖点（8-15 秒）

**【画面】** 真人出镜，指着 Mac 屏幕

**【字幕】**
> 我搞了一套全自动系统
> 录音 / 视频链接 → AI 笔记
> 零手动操作

**【旁白】**
"我用 Python + Claude AI 搞了一套自动化系统。录音自动转录总结，社交媒体链接自动整理。全程零手动。"

---

## 镜头 3 — 演示 1：Telegram 发链接（15-35 秒）

**【画面】** 手机屏幕录制
1. 打开 Telegram，发一条 YouTube 链接
2. Bot 回复「⏳ 处理中...」
3. 切到 Mac — Obsidian 里弹出一篇整理好的笔记

**【字幕】**
> Telegram 发链接 → 自动下载 → AI 分析
> YouTube 字幕直接抓，不用下载整个视频

**【旁白】**
"在 Telegram 发一条 YouTube 链接。系统自动抓字幕、AI 分析，几秒钟后 Obsidian 里就出现整理好的深度笔记 — 核心观点、按主题整理的大纲、行动项，全自动。"

---

## 镜头 4 — 演示 2：短视频拆解（35-50 秒）

**【画面】** 手机屏幕录制
1. Telegram 发一条抖音链接
2. 切到 Obsidian — 短视频框架拆解笔记

**【字幕 — 快速闪切笔记内容】**
> Hook 类型：痛点式
> 内容结构：递进式
> CTA：关注引导
> 可复制公式：一句话总结

**【旁白】**
"发一条抖音链接，系统自动拆解内容框架 — Hook 怎么开头、内容怎么推进、结尾怎么引导。还给你总结出一句可复制公式，直接套用就能拍。"

---

## 镜头 5 — 演示 3：录音自动转录（50-70 秒）

**【画面】**
1. 手持 Plaud 录音笔 / iPhone 录音界面
2. 录音结束
3. Mac 菜单栏 icon 显示「转录中...」
4. Obsidian 弹出会议纪要

**【字幕】**
> 录完音 → 自动同步 → 本地 Whisper 转录
> → Claude AI 分类 + 总结 → Obsidian
> 全程零操作

**【旁白】**
"录音也一样。Plaud 录完，自动同步到 Mac。本地 Whisper 转录 — 不上传任何数据，GPU 加速几分钟搞定。然后 Claude 自动判断这是会议还是备忘，生成对应格式的笔记。"

---

## 镜头 6 — 幕后揭秘（70-85 秒）

**【画面】** Mac 屏幕录制，快速展示：
1. 终端 `tail -f /tmp/auto-transcribe-out.log` — 实时日志滚动
2. 菜单栏 App 状态
3. `prompts/` 文件夹 — 14 个 prompt 模板一闪而过
4. Obsidian Vault — social-captures/ 和 recording-notes/ 两个文件夹

**【字幕】**
> Python 3.13 + Whisper (本地 GPU)
> Claude CLI + 14 个场景化 Prompt
> macOS launchd 5 个后台服务
> 全开源

**【旁白】**
"技术栈：Python、本地 Whisper、Claude CLI。14 个场景化 prompt 模板 — 会议、课堂、短视频拆解、广告分析，每种内容都有专属格式。5 个 launchd 后台服务，Mac 开机自动跑。"

---

## 镜头 7 — 广告分析彩蛋（85-95 秒）

**【画面】** 手机录制
1. Telegram 发 `ad https://instagram.com/reel/xxx`
2. Obsidian 弹出广告分析笔记：Hook 手法 / 卖点呈现 / CTA 类型 / 可借鉴的创意点

**【字幕】**
> 加 "ad" 前缀 → 自动切换广告分析模式
> 拆解 Hook / 卖点 / CTA / 可借鉴点

**【旁白】**
"彩蛋：链接前面加个 ad，系统自动切换广告分析模式。帮你拆解竞品广告的创意手法。"

---

## 镜头 8 — CTA（95-110 秒）

**【画面】** 真人出镜 + 屏幕分屏展示 Obsidian 笔记库

**【字幕】**
> 你的第二大脑不应该靠手动记
> 代码开源 / 链接在评论区

**【旁白】**
"从今天起，看过的每个视频、开过的每个会、听过的每个播客，全部自动变成你的知识库。代码开源，链接放评论区。关注我，下一期教你怎么搭。"

---

## 拍摄清单

- [ ] Mac 屏幕录制软件准备好（推荐 OBS 或 CleanShot X）
- [ ] iPhone 屏幕录制打开
- [ ] Telegram Bot 确认正常运行
- [ ] 准备 3 个测试链接：YouTube 长视频、抖音短视频、Instagram 广告
- [ ] Obsidian 打开 social-captures/ 文件夹
- [ ] 终端打开 `tail -f /tmp/auto-transcribe-out.log`
- [ ] 菜单栏 App 运行中
- [ ] 准备一段 30 秒的测试录音（用 Plaud 或 Voice Memos）

## 剪辑建议

- **节奏**：每个镜头 8-15 秒，不要停留太久
- **转场**：用快速切换（cut），不要花哨转场
- **字幕**：大字、白底黑字或黑底白字，关键词用高亮色
- **BGM**：轻快电子/Lo-fi，不要太吵
- **画面比例**：9:16 竖屏（抖音/小红书），正方形裁切（Threads）
- **封面**：「我搞了一套 AI 自动笔记系统」+ Mac 屏幕截图

## 标题/文案参考

**抖音/小红书：**
> 我搞了一套 AI 自动笔记系统，录音和视频全部自动变笔记！零手动 🔥

**Threads：**
> Built a fully automated note-taking system with Python + Claude AI.
> Send any link to Telegram → structured notes in Obsidian.
> Record a meeting → auto-transcribed + summarized.
> Zero manual steps. Open source.

**标签：** #AI自动化 #Obsidian #ClaudeAI #Python #效率工具 #第二大脑 #录音转录 #开发者日常
