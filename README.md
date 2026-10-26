# xhs-humanlike

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](https://www.python.org/)

用 adb 注入触摸事件，模拟真人刷小红书「发现」页的行为。

不 root、不装 App、不 hook、不读应用私有数据 —— 手机端零安装，全部动作都是 Android 系统自带的标准输入事件。
核心不是"能滑"，而是**滑得像人**：会连着快划十几屏什么都不看，会在一篇里停留一分半，会把评论区一路翻到底，也会把手机放下发呆。

```
▸ 正常浏览  (精力 0.54)
  📖 阅读《哪个动漫的主角人设是纯出生》 图1 · 停 8s
  📖 看评论《绝命毒师里的Jane，这长相真的很吸血鬼》 图7 · 停 11s 翻图1
  📖 点错了《纽约小狗万圣节🎃》 立刻退回
▸ 休息  (精力 0.41)
  ☕ 歇 47s（人不看了）
▸ 沉浸细看  (精力 0.63)
  📖 看图《不敢相信这是香港水果的物价》 图7 · 停 40s 翻图6
```

---

## ⚠️ 先读这一段

本项目**仅供个人学习与技术研究**，用于理解 Android 输入注入机制、无障碍节点树解析和行为建模。

- 请只在你**自己的账号**上、以**低频**方式使用，把它当作"帮我翻着看"的工具，不要当作涨粉/刷量的自动化运营工具。
- 平台有风控机制（设备指纹、行为频率分析、验证码）。**任何自动化行为都有被判定异常的风险，账号风险由使用者自负。**
- 不要用于批量操作、引流、刷互动、抢购或任何违反平台用户协议的场景。
- 项目不采集、不上传任何数据，全部逻辑跑在本地，不会向任何服务器发送内容。

如果你看到这段觉得不适合自己的使用场景，请直接关掉这个页面。

---

## 它到底做了什么

三个层次，缺一不可：

```
① 观测   uiautomator dump  →  拉回无障碍节点树 XML
         dumpsys window    →  当前前台是哪个 Activity
              ↓
② 决策   行为模型（精力状态 + 标题内容 + 概率分布）决定下一步做什么
              ↓
③ 执行   adb shell input tap / motionevent / keyevent
              ↓
         回到 ①，校验页面是否如预期，不对就按返回兜底
```

**为什么用节点树而不是截图**：小红书信息流的每张卡片在无障碍树里都带 `content-desc`，格式是 `笔记  标题  来自作者  赞数`。直接读文本比 OCR 快一个数量级，还能拿到精确的卡片坐标和图片张数（`共N张`）。

**为什么不用 uiautomator2 / Appium**：它们要在手机上装 server。本项目手机端零安装，只依赖 adb 和系统自带的 `input` 命令，拔了线不留痕迹。

---

## 环境要求

| 项目 | 要求 |
|---|---|
| 电脑 | macOS 实测通过；Windows 已完成适配（未在真机验证）；Linux 未验证 |
| Python | 3.8+，**不需要任何第三方包** |
| adb | Android platform-tools（见下方安装） |
| 手机 | Android 8+，已开启开发者选项与 USB 调试 |
| 小红书 | 已安装并**已登录**，能正常刷「发现」页 |
| Root | **不需要** |

装 adb：

```bash
# macOS
brew install --cask android-platform-tools

# Windows / Linux
# 从 https://developer.android.com/tools/releases/platform-tools 下载解压，
# 把 platform-tools 目录加进 PATH
```

手机端准备：设置 → 关于手机 → 连点「版本号」7 次 → 返回设置 → 开发者选项 → 打开「USB 调试」→ 插线 → 手机上点「允许 USB 调试」。

**Windows 用户**：命令里的 `python3` 换成 `python`；先执行一次 `set PYTHONUTF8=1`（否则日志里的
中文和 emoji 在重定向到文件时会报编码错误）。详见 [docs/FAQ.md](docs/FAQ.md) 的「Windows 上的问题」。

---

## 快速开始

```bash
git clone https://github.com/Celax-02/xhs-humanlike.git
cd xhs-humanlike

# 1. 先自检，逐项告诉你哪里不通
python3 doctor.py

# 2. 看它会做什么（不真的操作手机）
python3 xhs_auto.py --dry-run -t 2 --verbose

# 3. 真正跑
python3 xhs_auto.py -t 20 --like 0.03 --fav 0.01
```

跑的过程中手机上会**自动保持屏幕常亮**（`svc power stayon usb`），结束时恢复。
按 `Ctrl+C` 随时停；被强杀（kill -9）时不会执行恢复，手动执行一次 `adb shell svc power stayon false` 即可。

---

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `-t, --minutes` | 20 | 运行时长（分钟） |
| `--like` | 0.03 | 每篇帖子点赞概率 |
| `--fav` | 0.01 | 每篇帖子收藏概率 |
| `--verbose` | 关 | 打印每一屏的滑动细节 |
| `--dry-run` | 关 | 只打印手势参数，不注入事件（仍需连设备读界面） |
| `--classic` | 关 | 退回 `input swipe` 匀速直线滑动（老设备兼容 / 对照用） |
| `--seed` | 无 | 固定随机种子，可复现同一场行为 |
| `-s, --serial` | 自动 | 多设备时指定序列号 |
| `--adb` | `adb` | adb 可执行文件路径 |

互动概率建议不超过 5%。真人刷 20 分钟通常点赞个位数，`--like 0.1` 就意味着平均每十篇点一次，明显偏高。

---

## 一次运行的输出

```
设备 1080x2340 · 上限 20 分钟 · 曲线触摸 · Ctrl+C 停止

▸ 快速划过  (精力 0.28)
    ⚡ 快滑 1/13 · 停 1.4s
    ⚡ 快滑 2/13 · 停 3.7s
    ...
▸ 正常浏览  (精力 0.33)
  📖 扫一眼《热心市民在中网现场接受采访》 图10 · 停 4s
  📖 看图《不敢相信这是香港水果的物价》 图7 · 停 40s 翻图6
  📖 看评论《绝命毒师里的Jane》 图7 · 停 11s 翻图1 下划9
  📖 点错了《纽约小狗万圣节🎃》 立刻退回

—— 收工 ——
划过 100 屏 · 看了 31 篇 · 翻图 13 张 · 评论下划 35 次 · 点赞 2 · 收藏 1
单篇平均停留 22.1s · 最长 63s · 总时长 20.2 分钟
```

`精力` 是行为模型的内部状态（0~1），会随机游走，用来串联整场行为；它本身不需要你干预，但看日志时能解释"为什么这一段一直在快划"。

---

## 目录结构

```
xhs-humanlike/
├── xhs_auto.py           主脚本，单文件无依赖
├── doctor.py             环境自检，跑之前先跑这个
├── docs/
│   ├── BEHAVIOR.md       行为模型详解与调参手册（想改得更像人就改这里）
│   ├── AGENT.md          用 AI Agent 运行本项目时的提示词模板
│   └── FAQ.md            常见问题与故障排查
├── examples/
│   └── sample-run.log    一次真实 20 分钟运行的完整日志
├── LICENSE
└── README.md
```

---

## 想改得更像人？

行为模型被拆成几个可以单独调的部件，全部集中在 `xhs_auto.py` 的「注意力模型」和「行为段」两节：

- 停留时长分布 → `dwell_time()`
- 看评论的深度 → `comment_depth()`
- 滑几屏才点开一篇 → `seg_drift()` 里的 `quota`
- 四种滑动力度 → `feed_swipe()` 的 `flick / normal / half / tiny`
- 全局精力漂移速度 → `main()` 里的 `random.gauss(0, 0.17)`

具体每一项的数学形式和调参建议见 [docs/BEHAVIOR.md](docs/BEHAVIOR.md)。

---

## 适配其他机型

坐标全部按 1080×2340 写，运行时读 `wm size` 自动等比缩放，绝大多数手机直接能用。

画幅差异特别大（比如平板、折叠屏展开态）时，等比缩放会失真，需要手动调 `feed_swipe()` 和 `detail_scroll()` 里的纵向范围（`FEED_TOP` / `FEED_BOTTOM` 是参考分辨率下的值）。

跑之前用 `python3 doctor.py` 看它有没有识别到笔记卡片，识别到了基本就没问题。

## 适配其他 App

不通用。每张卡片的 `content-desc` 格式、页面切换时 Activity 名、返回键层级、双击点赞的落点，都是逐个 App 单独摸出来的。换 App 要重新做一遍观测，工作量和这个项目当初一样。

---

## 已知限制

- **设备必须解锁**。相机或 adb 都无法替代锁屏密码，脚本检测到锁屏只会提示你去手动解锁。
- **`uiautomator dump` 有 1 秒级耗时**。每读一次界面约 0.7~1.2 秒，所以脚本的"反应速度"比真人慢，这个延迟被计入了停留时间，看起来反而更自然。
- **小红书版本更新可能改变 `content-desc` 格式**，那时 `doctor.py` 会报"读不到笔记卡片"。修复位置在 `feed_notes()` 的正则。
- **不支持直播间**。信息流里的直播卡片会被跳过（点进去退出逻辑完全不同）。
- 部分国产 ROM 会限制后台 shell 权限或强制杀 adb 连接，需要关掉电池优化。

---

## License

MIT，见 [LICENSE](LICENSE)。

---

## English quick start

`xhs-humanlike` simulates a human scrolling Xiaohongshu (RedNote) by injecting standard Android touch
events through `adb`. No root, no app installed on the phone, no hooks, no access to private app data.

```bash
# Requirements: Python 3.8+, Android platform-tools, USB debugging enabled
python3 doctor.py                      # environment self-check
python3 xhs_auto.py --dry-run -t 2     # dry run: decision only, no touch events
python3 xhs_auto.py -t 20 --like 0.03 --fav 0.01
```

The behaviour model (log-normal dwell times, exponential comment depth, energy random-walk, four swipe
types with curved multi-point touch injection) is documented in [docs/BEHAVIOR.md](docs/BEHAVIOR.md).
Prompt templates for running this project with an AI agent are in [docs/AGENT.md](docs/AGENT.md).

For personal study of input injection and behaviour modelling only. You are responsible for complying
with the platform's terms of service.
