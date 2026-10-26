# 常见问题与故障排查

排查顺序永远是：`adb devices -l` → `python3 doctor.py` → 看具体哪一项不通。

---

## 连不上手机

### `adb devices` 列表是空的

按顺序排除：

1. **数据线**。很多线只能充电。换一根，最好是手机原装线。
2. **USB 调试没开**。设置 → 开发者选项 → USB 调试。
3. **没有授权**。拔插一次数据线，看手机有没有弹「允许 USB 调试」。弹了要点允许，并且勾上「一律允许」。
4. **macOS 提示「USB 配件需要解锁」**。先解锁手机再插线。
5. **USB 模式不对**。下拉通知栏把「USB 用途」从「仅充电」改成「文件传输」。

### 状态是 `unauthorized`

```
21982877	unauthorized
```

授权没给。手机上会有一个弹窗，点允许。如果没弹窗：

```bash
adb kill-server
adb start-server
adb devices
```

还不行就删掉电脑上的密钥重新授权：

```bash
rm ~/.android/adbkey ~/.android/adbkey.pub
adb kill-server && adb devices
```

手机会重新弹窗。

### 状态是 `offline`

```bash
adb kill-server
adb devices
```

还 offline 就拔插数据线，或者重启手机的 USB 调试开关。

### 多台设备

```bash
adb devices -l
python3 xhs_auto.py -s 21982877 -t 20
```

或者设置环境变量 `export ANDROID_SERIAL=21982877`，`doctor.py` 也会读它。

---

## `doctor.py` 报「读不到笔记卡片」

这是最常见的真问题。分三种情况：

### ① 不在信息流页面

先把小红书打开、停在「发现」页，再跑自检。脚本启动时会自己尝试退回信息流，但自检那一刻页面不对就会报这个。

### ② 小红书改版了

无障碍描述格式变了。手动看一眼现在的格式：

```bash
adb shell uiautomator dump /sdcard/t.xml
adb pull /sdcard/t.xml /tmp/t.xml
```

在 `/tmp/t.xml` 里搜 `content-desc`，找到卡片节点的原文。原来是：

```
笔记  标题文字  来自作者名  1234
```

`feed_notes()` 里对应两处需要改：

```python
if not d.startswith("笔记"):          # ← 卡片前缀
m = re.match(r"^笔记\s+(.+?)\s+来自", d)   # ← 标题与「来自」之间的分隔
```

把新格式套进去即可。

### ③ 系统禁用了 uiautomator

部分 ROM 会限制。测试：

```bash
adb shell uiautomator dump /sdcard/t.xml
```

返回 `ERROR: could not get idle state` 或直接失败，说明被限制。这种情况只能换设备或换系统。

---

## 脚本跑了但手机没反应

按这个顺序查：

1. `adb shell dumpsys window | grep mCurrentFocus` —— 前台真的是小红书吗？
2. 手机是不是**黑屏**？`adb shell dumpsys power | grep mWakefulness`，
   不是 `Awake` 就先 `adb shell input keyevent 224` 唤醒。
3. 是不是**锁屏**？`adb shell dumpsys window | grep isKeyguardShowing`。
   是 `true` 就必须手动解锁，adb 解不开。
4. 用 `--dry-run` 看脚本是否在正常决策（不注入事件但要能读界面）。

---

## 中途卡住 / 掉到桌面 / 进了直播间

**掉到桌面**：通常是某次点击落点算错，或者返回键多按了。脚本每段开始都有 `ensure_feed()` 兜底，会尝试 `back()` 或 `monkey` 拉起 App。如果连续两次都救不回来，它会在日志里打印：

```
❌ 没能回到信息流。请确认：手机已解锁、小红书已打开、USB 调试已授权。
```

**进了直播间**：信息流里的直播卡片，`content-desc` 不以「笔记」开头，理论上会被跳过。如果还是进去了，退出方式和帖子不同（直播间的返回层级更深），脚本的兜底返回会连按 3 次，通常也能出来。出来之后不影响继续跑。

**卡在同一屏反复识别到相同的卡片**：如果日志里 `点错了` 连续出现，通常是某张卡片的坐标落到了不可点区域。检查 `feed_notes()` 里的落点范围：

```python
cx = x1 + (x2 - x1) * random.uniform(0.28, 0.62)   # 横向
cy = y1 + (y2 - y1) * random.uniform(0.22, 0.50)   # 纵向，偏上避开点赞按钮
```

---

## 点赞点了但没生效

小红书的点赞按钮在底部操作栏，位置会随页面滚动变化。脚本用 `action_points()` 动态查找（按 `content-desc` 以「点赞」开头，取 y 最大的那个节点）。

如果日志里出现了 `♥赞` 但手机上没有红心：

- 可能点在了「赞」和「评论」之间的空隙
- 可能是双击点赞（信息流里）和单击（详情页）的机制不同，脚本用的是详情页单击

手动验证一次坐标对不对：

```bash
adb shell uiautomator dump /sdcard/t.xml
adb pull /sdcard/t.xml /tmp/t.xml
grep -o 'content-desc="点赞[^"]*"[^>]*bounds="[^"]*"' /tmp/t.xml
```

---

## 屏幕常亮没恢复

脚本正常结束时会执行 `adb shell svc power stayon false`。但这几种情况下不会执行：

- `Ctrl+C` 中断（会有部分收尾，但不保证）
- 进程被 `kill -9` 强杀
- adb 连接中途断开

手动恢复：

```bash
adb shell svc power stayon false
```

`stayon usb` 只在插着 USB 时生效，所以**直接拔线也能恢复**，没有副作用。

---

## Windows 上的问题

代码层面已经做了 Windows 适配（临时目录、控制台编码、adb 路径候选），但**没有在 Windows 真机上跑过完整的一次**。
下面三步是在 PowerShell / cmd 里必须注意的（`python` 而不是 `python3`）：

```bat
:: 1) 让中文和 emoji 正常输出（否则重定向到文件时会 UnicodeEncodeError）
set PYTHONUTF8=1

:: 2) 确认 adb 在 PATH 里；没加的话用 --adb 指定
adb version
python doctor.py --adb "C:\platform-tools\adb.exe"

:: 3) 用 python 而不是 python3（Windows 上通常没有 python3 这个命令）
python doctor.py
```

其他注意点：

- **`python3` 不存在**。用 `python`，或者装了 Python Launcher 的话用 `py -3`。README 里所有
  `python3 xxx.py` 在 Windows 上都换成 `python xxx.py`。
- **中文/emoji 乱码**：`chcp 65001` 只影响当前窗口；`set PYTHONUTF8=1` 更彻底。如果是永久设置，
  在「系统属性 → 环境变量」里加 `PYTHONUTF8=1`。
- **路径带空格要加引号**：`--adb "C:\Program Files\platform-tools\adb.exe"`。
- **临时文件**：脚本的 UI dump 落在系统临时目录（`%TEMP%`，通常是
  `C:\Users\你\AppData\Local\Temp`）。如果你的用户名是中文、`adb pull` 报路径错误，把
  `TEMP` 环境变量指到一个纯英文目录（比如 `D:\tmp`）再跑。
- **无线调试**（Android 11+）在 Windows 上一样可用，`adb pair` / `adb connect` 命令不变。

---

## 想用无线调试

Android 11+ 支持：

```bash
# 手机: 开发者选项 → 无线调试 → 使用配对码配对设备
adb pair 192.168.1.100:37123      # 输入配对码
adb connect 192.168.1.100:5555
adb devices
```

连上之后 `python3 doctor.py` 照常。注意无线连接**延迟更高**（每次 `uiautomator dump` 会慢 2~3 倍），脚本整体节奏会变慢 —— 因为延迟被计入了停留时间，看起来反而更自然，但 20 分钟能刷的篇数会少一些。

---

## 会不会被封号

诚实回答：**有风险，没人能保证**。

平台的风控不止看手势时序，还包括设备指纹、账号历史行为、内容偏好一致性、IP 变化等。这个项目只处理了最表层的一维（手势和节奏），它能降低"一眼看出是脚本"的概率，但不可能保证安全。

降低风险的实用建议：

- 互动频率压到最低。`--like 0.02 --fav 0.005` 已经很够
- 单次不超过 30 分钟，每天别跑太多次
- 别在登录后立刻跑，先手动刷几分钟
- 出现验证码、滑块、异常提示就停手，隔几小时再说
- 新号、刚改过密码的号不要马上用

**任何声称"完全绕过风控"的说法都是骗人的。** 这个项目的定位是"帮我翻着看"，不是"帮我涨粉"。

---

## 能改成其他 App 吗

不通用。每张卡片的 `content-desc` 格式、页面切换的 Activity 名、返回键层级、双击落点，都是逐个 App 单独观测出来的。

要换 App，需要重新做一遍：

1. 反复 `uiautomator dump` 搞清楚卡片节点的文本格式和 bounds
2. 摸清页面栈（哪个 Activity 是列表、哪个是详情、返回几次能回来）
3. 重写 `feed_notes()`、`focus()` 里的常量，以及 `open_note()` 的动作分支

工作量和你现在看到的这个仓库差不多。
