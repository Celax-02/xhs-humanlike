#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs-humanlike —— 拟人化小红书自动浏览

用 adb 向手机注入标准触摸事件，模拟真人刷「发现」页的行为：
滑动、点开帖子、翻图、翻评论、偶尔点赞收藏、偶尔发呆。
不 root、不注入进程、不 hook、不读应用私有数据，手机上不装任何东西。

依赖：Python 3.8+ / adb（platform-tools）。无第三方 Python 包。

用法:
    python3 xhs_auto.py                      # 默认 20 分钟
    python3 xhs_auto.py -t 40                # 40 分钟
    python3 xhs_auto.py --like 0.03 --fav 0.01
    python3 xhs_auto.py --verbose            # 打印每一屏细节
    python3 xhs_auto.py --dry-run            # 只打印手势参数，不真的注入
    python3 xhs_auto.py -s 21982877          # 指定设备序列号

完整说明见 README.md，行为模型与调参见 docs/BEHAVIOR.md。
"""

import subprocess, random, time, argparse, re, math, sys, os, shutil, tempfile
import xml.etree.ElementTree as ET

# ================= 配置 =================
ADB = "adb"                      # adb 路径；不在 PATH 里会自动在常见位置查找
# 临时文件放系统临时目录：Windows 上没有 /tmp，写死会让 adb pull 失败
LOCAL_UI = os.path.join(tempfile.gettempdir(), "_xhs_ui.xml")

PKG = "com.xingin.xhs"           # 小红书包名
ACT_FEED = "IndexActivityV2"     # 信息流 Activity
ACT_DETAIL = "NoteDetailActivity"  # 帖子详情 Activity
ACT_SEARCH = "GlobalSearchActivity"  # 搜索页（搜索入口和结果列表都在这）

# 所有手势坐标都按 1080x2340 这台参考机写死，运行时按实际分辨率等比缩放
BASE_W, BASE_H = 1080, 2340
SW, SH = BASE_W, BASE_H

FEED_TOP, FEED_BOTTOM = 210, 2145   # 信息流卡片可点击的纵向范围（参考分辨率下）

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

SERIAL = None
USE_MOTION = True                # False 时退回 input swipe 匀速直线
DRY = False                      # True 时只打印不注入


# ================= adb 基础 =================
ADB_CANDIDATES = [
    "~/Library/Android/sdk/platform-tools/adb",       # macOS 默认
    "~/Android/Sdk/platform-tools/adb",               # Linux 默认
    "~/AppData/Local/Android/Sdk/platform-tools/adb.exe",  # Windows 默认
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
]


def resolve_adb(explicit=None):
    """--adb > 环境变量 ADB > PATH > 常见安装位置"""
    for c in (explicit, os.environ.get("ADB")):
        if c:
            return c
    if shutil.which("adb"):
        return shutil.which("adb")
    for c in ADB_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.exists(p):
            return p
    return "adb"


def list_devices():
    """返回当前已授权且在线的设备序列号列表（不带 -s 执行，不受 SERIAL 影响）"""
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def ui_tmp_path():
    """每个进程（每台设备）用独立的临时文件，否则并行跑会互相覆盖 XML"""
    tag = re.sub(r"[^0-9A-Za-z_.-]", "_", SERIAL) if SERIAL else f"pid{os.getpid()}"
    return os.path.join(tempfile.gettempdir(), f"_xhs_ui_{tag}.xml")


def ad(*args, timeout=25):
    cmd = [ADB]
    if SERIAL:
        cmd += ["-s", str(SERIAL)]
    cmd += [str(a) for a in args]
    if DRY:
        # 只读命令照常执行，写操作（input）跳过
        if args and args[0] == "shell" and len(args) > 1:
            joined = " ".join(str(a) for a in args[1:])
            if joined.startswith("input "):
                return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        sys.exit(f"找不到 adb（{ADB}）。\n"
                 "请安装 Android platform-tools，或用 --adb 指定完整路径。\n"
                 "更详细的环境检查：python3 doctor.py")
    except subprocess.TimeoutExpired:
        return None


def sh(*args, timeout=25):
    return ad("shell", *args, timeout=timeout)


def back():
    sh("input", "keyevent", "4")


# ================= 坐标缩放 =================
def sx(v):
    return int(round(v * SW / BASE_W))


def sy(v):
    return int(round(v * SH / BASE_H))


def detect_screen():
    """读设备真实分辨率，用于把参考坐标换算过去"""
    global SW, SH
    r = sh("wm", "size")
    m = re.search(r"(\d+)\s*x\s*(\d+)", (r.stdout if r else "") or "")
    if m:
        SW, SH = int(m.group(1)), int(m.group(2))
    return SW, SH


# ================= 触摸：曲线 + 缓动 =================
def _ease(t, kind):
    """把线性时间轴弯成速度曲线"""
    if kind == "smooth":            # 慢起 → 快 → 慢，最像手指
        return t * t * (3 - 2 * t)
    if kind == "out":               # 快起 → 减速，像甩一下
        return 1 - (1 - t) ** 2.4
    if kind == "in":                # 慢起 → 加速，像拖动重物
        return t ** 1.9
    return t * t * (3 - 2 * t)


def drag(x1, y1, x2, y2, dur_ms, bow=None, ease="smooth", steps=None):
    """
    分段曲线拖拽。坐标已是真实像素，调用方负责缩放。
    dur_ms 是手指在屏上的总时长；bow 是横向弓形偏移（px）。
    """
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    if DRY:
        print(f"      <drag {x1},{y1} → {x2},{y2}  {int(dur_ms)}ms  {ease}"
              f"{'' if bow is None else '  bow=%.0f' % bow}>", flush=True)
        return
    if not USE_MOTION:
        sh("input", "swipe", x1, y1, x2, y2, int(dur_ms))
        return

    if steps is None:
        steps = max(5, min(24, int(dur_ms / 27)))
    if bow is None:
        # 多数时候几乎笔直，偶尔明显偏一点
        mag = random.choice([0, 0, random.uniform(3, 9), random.uniform(9, 24)])
        bow = random.choice([-1, 1]) * mag

    gap_ms = dur_ms / steps
    slp = ""
    if gap_ms > 44:                 # 慢速拖拽才需要显式等待
        slp = f"sleep {max(0.0, (gap_ms - 30) / 1000):.3f}; "

    parts = [f"input motionevent DOWN {x1} {y1}"]
    for i in range(1, steps):
        t = i / steps
        e = _ease(t, ease)
        x = x1 + (x2 - x1) * e + bow * math.sin(math.pi * e)
        y = y1 + (y2 - y1) * e + random.uniform(-1.3, 1.3)
        parts.append(f"{slp}input motionevent MOVE {int(x)} {int(y)}")
    parts.append(f"input motionevent UP {x2} {y2}")
    ad("shell", "; ".join(parts), timeout=20)


def tap(x, y, hesitate=True):
    """点一下。hesitate 模拟『想点但犹豫一下』"""
    if hesitate and random.random() < 0.35:
        time.sleep(lognorm(0.45, 0.7, 0.1, 2.2, lo_clip=False))
    if DRY:
        print(f"      <tap {int(x)},{int(y)}>", flush=True)
        return
    sh("input", "tap", int(x), int(y))


# ================= 分布工具 =================
def lognorm(median, sigma=0.5, lo=0.0, hi=1e9, lo_clip=True):
    """对数正态：以 median 为中位数，重尾偏高。人的行为时长基本都是这个形状"""
    v = median * math.exp(random.gauss(0, sigma))
    return max(lo, min(hi, v)) if lo_clip else max(0.0, v)


def geom(p, lo=0, hi=999):
    """几何分布：抽到第一次成功所需的次数，用于『滑几屏才点开』"""
    n = 0
    while random.random() > p and n < hi:
        n += 1
    return max(lo, min(hi, n))


def chance(p):
    return random.random() < p


# ================= 页面判断 =================
def focus():
    if DRY:
        return "feed"
    r = sh("dumpsys", "window")
    if not r:
        return "unknown"
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ (\S+?)\}", r.stdout)
    if not m:
        return "unknown"
    w = m.group(1)
    if ACT_DETAIL in w:
        return "detail"
    if ACT_SEARCH in w:
        return "search"
    if ACT_FEED in w:
        return "feed"
    return "other"


def dump_ui():
    for _ in range(3):
        r = sh("uiautomator", "dump", "/sdcard/_ui.xml", timeout=20)
        if r and "dumped" in (r.stdout or ""):
            ad("pull", "/sdcard/_ui.xml", LOCAL_UI, timeout=20)
            sh("rm", "/sdcard/_ui.xml")
            try:
                return ET.parse(LOCAL_UI).getroot()
            except Exception:
                pass
        time.sleep(0.4)
    return None


def parse_bounds(s):
    m = BOUNDS_RE.search(s or "")
    return tuple(map(int, m.groups())) if m else None


def feed_notes(root):
    """从无障碍节点树读当前屏可见的笔记卡片"""
    out = []
    top, bottom = sy(FEED_TOP), sy(FEED_BOTTOM)
    for n in root.iter("node"):
        d = (n.get("content-desc") or "").strip()
        if not d.startswith("笔记"):
            continue                       # 直播/视频卡片跳过，退出行为不一样
        b = parse_bounds(n.get("bounds"))
        if not b:
            continue
        x1, y1, x2, y2 = b
        if y1 < top or y2 > bottom or (y2 - y1) < sy(200):
            continue
        m = re.match(r"^笔记\s+(.+?)\s+来自", d)
        title = m.group(1).strip() if m else d[2:].strip()
        if len(title) < 2:
            continue
        # 落点：偏中上，避开右下角互动按钮；每次位置独立抖动
        cx = x1 + (x2 - x1) * random.uniform(0.28, 0.62)
        cy = y1 + (y2 - y1) * random.uniform(0.22, 0.50)
        out.append((title, int(cx), int(cy)))
    return out


def search_notes(root):
    """
    搜索结果页是双列瀑布流，卡片容器没有 content-desc。
    只能靠「可点击的大块矩形」定位卡片，再用落到卡片内的文字节点取标题。
    """
    texts, cards = [], []
    for n in root.iter("node"):
        b = parse_bounds(n.get("bounds"))
        if not b:
            continue
        t = (n.get("text") or "").strip()
        if t:
            texts.append((b, t))
        if n.get("clickable") == "true":
            w, h = b[2] - b[0], b[3] - b[1]
            if w >= 470 and 650 <= h <= 1600:
                cards.append(b)

    out = []
    for x1, y1, x2, y2 in cards:
        inside = [(b, t) for b, t in texts
                  if b[0] >= x1 - 10 and b[2] <= x2 + 10
                  and b[1] >= y1 - 10 and b[3] <= y2 + 10]
        if not inside or any(t == "广告" for _, t in inside):
            continue
        cand = [t for _, t in inside if len(t) >= 6]
        if not cand:
            continue
        title = max(cand, key=len)
        cx = int((x1 + x2) / 2 + random.uniform(-40, 40))
        cy = int(y1 + (y2 - y1) * random.uniform(0.25, 0.45))
        out.append((title, cx, cy))
    return out


def action_points():
    """动态找详情页底部的点赞/收藏/评论按钮位置（滚动后位置会变）"""
    pts = {}
    root = dump_ui()
    if root is None:
        return pts
    for n in root.iter("node"):
        d = (n.get("content-desc") or "").strip()
        b = parse_bounds(n.get("bounds"))
        if not b or not d:
            continue
        key = None
        if d.startswith("点赞"):
            key = "like"
        elif d.startswith("收藏"):
            key = "fav"
        elif d.startswith("评论"):
            key = "cmt"
        if not key:
            continue
        c = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
        if key not in pts or c[1] > pts[key][1]:      # 取最靠下的（底部操作栏）
            pts[key] = c
    return pts


# ================= 信息流滑动 =================
def feed_swipe(kind="normal"):
    """
    kind:
      flick  快速甩动，距离大、手指停留短
      normal 正常翻页
      half   滑到一半松手，页面回弹（人经常这样试探）
      tiny   几乎没动，像手抖
    """
    x = sx(random.randint(400, 690))

    if kind == "flick":
        y1 = random.randint(1780, 2080)
        travel = random.randint(550, 900)
        dur = random.randint(105, 185)
        drag(x, sy(y1), x + sx(random.randint(-45, 45)), sy(y1 - travel), dur,
             ease=random.choice(["out", "out", "smooth"]))
    elif kind == "half":
        y1 = random.randint(1700, 2000)
        travel = random.randint(180, 420)          # 不够翻页，会回弹
        dur = random.randint(200, 400)
        drag(x, sy(y1), x + sx(random.randint(-30, 30)), sy(y1 - travel), dur)
    elif kind == "tiny":
        y1 = random.randint(1200, 1600)
        drag(x, sy(y1), x, sy(y1 - random.randint(18, 60)), random.randint(120, 220))
    else:
        y1 = random.randint(1720, 2020)
        travel = random.randint(420, 780)
        dur = random.randint(190, 460)
        drag(x, sy(y1), x + sx(random.randint(-40, 40)), sy(y1 - travel), dur,
             ease=random.choice(["smooth", "smooth", "out", "in"]))


def feed_swipe_back():
    """往回翻：手指从上往下拖"""
    x = sx(random.randint(420, 660))
    y1 = random.randint(680, 980)
    drag(x, sy(y1), x + sx(random.randint(-30, 30)),
         sy(y1 + random.randint(220, 520)), random.randint(210, 420), ease="smooth")


# ================= 详情页动作 =================
def _travel(power):
    base = lognorm(560, 0.42, 180, 1500) * power
    if chance(0.08):                                 # 偶尔一下甩很大
        base *= random.uniform(1.4, 2.0)
    return min(1500, base)


def detail_scroll(power=1.0):
    """在帖子内部上滑：滚正文 / 展开评论 / 往下翻评论
    power 越大滑得越远越快
    """
    x = sx(random.randint(455, 625))
    y1 = random.randint(1680, 1980)
    travel = _travel(power)
    if power < 0.7:                                  # 慢拖，像一条条读
        dur = random.randint(900, 1600)
        ease = "smooth"
    elif power > 1.4:                                # 大甩
        dur = random.randint(280, 460)
        ease = "out"
    else:
        dur = random.randint(420, 760)
        ease = random.choice(["smooth", "smooth", "in"])
    drag(x, sy(y1), x + sx(random.randint(-22, 22)), sy(y1 - travel), dur, ease=ease)


def detail_scroll_back():
    x = sx(random.randint(465, 615))
    y1 = random.randint(880, 1150)
    drag(x, sy(y1), x, sy(y1 + random.randint(240, 520)),
         random.randint(340, 620), ease="smooth")


def flip_image(forward=True):
    y = random.randint(680, 1230)
    if forward:
        drag(sx(940), sy(y), sx(190), sy(y + random.randint(-70, 70)),
             random.randint(220, 360), ease="out")
    else:
        drag(sx(190), sy(y), sx(940), sy(y + random.randint(-70, 70)),
             random.randint(220, 360), ease="out")


# ================= 注意力模型 =================
STYLE_CN = {"glance": "扫一眼", "image": "看图", "read": "阅读", "comments": "看评论"}

INTEREST = re.compile(r"攻略|教程|干货|指南|怎么|如何|为什么|推荐|测评|盘点|原理|区别|"
                      r"科普|合集|清单|总结|全流程|保姆级|从0到1")
HELP = re.compile(r"求助|急|求救|怎么办|翻车|踩坑|失败|报错|卡住|不懂")
QUESTION = re.compile(r"[?？]")

# ================= 主题定向 =================
# 只做「在哪张卡上落手指」的偏好，不改任何客户端行为：
# 仍是在信息流里滑动、点开、停留，只是选帖时按标题匹配主题。
TOPIC_SETS = {
    # 裸 AI / RAG 必须加词边界，否则 email、detail、storage 之类会被误判成 AI 内容
    "ai": re.compile(
        r"(?<![A-Za-z])AI(?![A-Za-z])|AIGC|人工智能|大模型|语言模型|LLM|GPT|ChatGPT|OpenAI|"
        r"Claude|Gemini|DeepSeek|千问|Qwen|通义|豆包|文心|Kimi|智谱|混元|Llama|Mistral|"
        r"Grok|Sora|Midjourney|Agent|智能体|提示词|Prompt|(?<![A-Za-z])RAG(?![A-Za-z])|"
        r"微调|多模态|生成式|具身智能|算力|机器学习|深度学习|神经网络|Transformer|"
        r"Embedding|Copilot|Cursor|Codex|ComfyUI|Ollama|vLLM|HuggingFace|Suno|可灵|"
        r"文生图|图生图|文生视频|英伟达|NVIDIA|GPU|CUDA|数字人|开源模型|模型训练",
        re.I),
}

# 全局计数：本轮点开多少篇、其中多少篇命中主题
TOPIC_STATE = {"open": 0, "hit": 0}
TOPIC = None

# 搜索词只用 ASCII：adb 的 input text 打不出中文（需要额外装输入法）
SEARCH_KW = ["AI", "AI Agent", "ChatGPT", "DeepSeek", "Claude", "GPT", "LLM", "Agent"]


def hits_topic(title):
    return bool(TOPIC and TOPIC.search(title or ""))


def want_topic_hit(args):
    """
    这一篇「应不应该」点主题相关的。
    用配额式纠偏而不是固定概率：累计占比低于目标就更容易选中，
    高于目标就更容易放过 —— 单场几十篇的尺度上能稳住目标比例，
    又不是严格交替（加了 gauss 噪声）。
    """
    if not TOPIC:
        return False
    n, hit = TOPIC_STATE["open"], TOPIC_STATE["hit"]
    need = args.topic_ratio * (n + 1) - hit
    return chance(max(0.0, min(1.0, need + random.gauss(0, 0.22))))



def pick_style(title, energy, is_ai=False):
    """按标题内容和当前精力决定这一篇会怎么看"""
    w = {"glance": 1.0, "image": 1.0, "read": 1.0, "comments": 1.0}

    if INTEREST.search(title):
        w["read"] *= 1.9
        w["comments"] *= 1.2
        w["glance"] *= 0.45
    if QUESTION.search(title):
        w["comments"] *= 2.5        # 带问号的标题，人真的会去评论区找答案
        w["glance"] *= 0.6
    if HELP.search(title):
        w["comments"] *= 1.9
    if len(title) <= 8:
        w["glance"] *= 1.8          # 标题短，信息少，划走更快
    if len(title) >= 24:
        w["read"] *= 1.35
    if is_ai:                       # 冲着这个主题来的，就多看两眼
        w["read"] *= 1.45
        w["comments"] *= 1.25
        w["glance"] *= 0.55

    # 精力低的时候更容易只扫一眼，精力高才愿意读和翻评论
    w["read"] *= 0.55 + energy * 1.0
    w["comments"] *= 0.65 + energy * 0.9
    w["glance"] *= 1.75 - energy

    keys = list(w)
    return random.choices(keys, weights=[w[k] for k in keys])[0]


def dwell_time(style, energy, imgs):
    """这一篇会停多久（秒）。对数正态 + 精力修正 + 内容量修正"""
    base = {"glance": 2.4, "image": 4.2, "read": 9.5, "comments": 6.5}[style]
    sigma = {"glance": 0.42, "image": 0.50, "read": 0.52, "comments": 0.48}[style]
    lo, hi = {"glance": (1.2, 8.0), "image": (2.0, 26.0),
              "read": (3.5, 62.0), "comments": (2.5, 34.0)}[style]

    v = lognorm(base, sigma, lo, hi)
    v *= 0.70 + energy * 0.62                       # 精力低就看得快
    if imgs >= 6:
        v *= random.uniform(1.08, 1.35)
    if chance(0.06):                                # 偶尔被某篇彻底吸住
        v *= random.uniform(1.6, 2.6)
    return max(lo, min(hi, v))


def comment_depth(energy):
    """评论区会往下滑几次。指数分布，重尾，能到 20+"""
    r = random.random()
    if r < 0.22:                                    # 打开看看就退，不往下翻
        return 0
    if r < 0.40:                                    # 浅翻两条
        return random.randint(1, 3)
    n = 3 + int(random.expovariate(0.30 / max(0.4, energy)))   # 长尾
    return min(n, 24)


# ================= 打开一篇帖子 =================
def open_note(seen, args, energy, stats=None, source="feed"):
    """点开、看完、退出。失败返回 None。source: feed（信息流）或 search（搜索结果）"""
    root = dump_ui()
    if root is None:
        return None
    parser = search_notes if source == "search" else feed_notes
    notes = [x for x in parser(root) if x[0] not in seen]
    if not notes:
        return None

    # 选哪张卡：默认随机；开了主题定向就按配额纠偏来选
    is_ai = False
    if TOPIC:
        want = want_topic_hit(args)
        ai = [x for x in notes if hits_topic(x[0])]
        # 想看点相关的、但这一屏没有，就像人一样多划两屏找一下
        # （搜索结果页整页都是相关笔记，不需要找）
        tried = 0
        while want and not ai and tried < 3 and source == "feed":
            if stats is not None:
                stats["screens"] += 1
            feed_swipe(random.choices(["flick", "normal"], weights=[0.6, 0.4])[0])
            tried += 1
            time.sleep(lognorm(1.3, 0.5, 0.4, 3.6))
            root = dump_ui()
            if root is None:
                break
            notes = [x for x in feed_notes(root) if x[0] not in seen]
            if not notes:
                return None
            ai = [x for x in notes if hits_topic(x[0])]
        if want and ai:
            title, cx, cy = random.choice(ai)
            is_ai = True
        else:
            rest = [x for x in notes if not hits_topic(x[0])] if want else notes
            title, cx, cy = random.choice(rest or notes)
            is_ai = hits_topic(title)
    else:
        title, cx, cy = random.choice(notes)
        is_ai = hits_topic(title)

    tap(cx, cy)
    time.sleep(lognorm(1.5, 0.42, 0.7, 4.0))

    if focus() != "detail":
        return None
    seen.add(title)
    TOPIC_STATE["open"] += 1
    if is_ai:
        TOPIC_STATE["hit"] += 1

    # 误触：点开了才发现不是想看的，快速退回
    if chance(0.09):
        time.sleep(lognorm(0.9, 0.5, 0.4, 2.6))
        back()
        time.sleep(lognorm(1.1, 0.4, 0.6, 2.4))
        return {"title": title, "style": "glance", "imgs": 1, "flipped": 0,
                "scrolls": 0, "liked": False, "faved": False,
                "dwell": 0.9, "mistap": True, "ai": is_ai}

    style = pick_style(title, energy, is_ai)
    imgs = 1
    root = dump_ui()
    if root is not None:
        for n in root.iter("node"):
            m = re.search(r"共(\d+)张", n.get("content-desc") or "")
            if m:
                imgs = int(m.group(1))
                break

    flipped = scrolls = 0
    liked = faved = False
    t_in = time.time()

    # 点赞/收藏的时机也随机：有时读一半就点，有时看完才点
    # 主题相关的帖子更容易被点：冲着它来的，互动意愿本来就高
    like_now = args.like and chance(min(0.30, args.like * (1.9 if is_ai else 1.0)))
    fav_now = args.fav and chance(min(0.20, args.fav * (1.6 if is_ai else 1.0)))
    like_early = chance(0.45)

    def do_like():
        nonlocal liked, faved
        pts = action_points()
        if like_now and not liked:
            tap(*pts.get("like", (sx(590), sy(2157))), hesitate=False)
            liked = True
            time.sleep(lognorm(0.55, 0.35, 0.3, 1.6))
        if fav_now and not faved:
            tap(*pts.get("fav", (sx(779), sy(2157))), hesitate=False)
            faved = True
            time.sleep(lognorm(0.55, 0.35, 0.3, 1.6))

    dwell = dwell_time(style, energy, imgs)
    if is_ai:                                   # 相关内容看得久一点
        dwell *= random.uniform(1.12, 1.45)
    time.sleep(dwell * random.uniform(0.38, 0.62))
    if like_early:
        do_like()

    if style == "glance":
        if chance(0.22):
            detail_scroll(random.uniform(0.6, 0.9))
            scrolls += 1
            time.sleep(lognorm(1.6, 0.5, 0.6, 5.0))
        if chance(0.14):                            # 手滑碰了一下屏幕
            drag(sx(random.randint(400, 680)), sy(random.randint(1200, 1700)),
                 sx(random.randint(400, 680)), sy(random.randint(1150, 1600)), 150)

    elif style == "image":
        if imgs > 1:
            budget = min(imgs - 1, max(1, geom(0.42, 1, 9)))
            for i in range(budget):
                if focus() != "detail":
                    break
                flip_image(forward=True)
                flipped += 1
                time.sleep(lognorm(2.7, 0.55, 0.8, 11.0))
                if chance(0.12) and i > 0:          # 回看上一张
                    flip_image(forward=False)
                    time.sleep(lognorm(1.7, 0.5, 0.6, 5.0))
        time.sleep(lognorm(2.0, 0.5, 0.5, 7.0))

    elif style == "read":
        if imgs > 1 and chance(0.55):
            budget = min(imgs - 1, max(1, geom(0.5, 1, 7)))
            for _ in range(budget):
                if focus() != "detail":
                    break
                flip_image(forward=True)
                flipped += 1
                time.sleep(lognorm(3.0, 0.55, 1.0, 12.0))
        if chance(0.5):
            n = max(1, geom(0.5, 1, 4))
            for _ in range(n):
                if focus() != "detail":
                    break
                detail_scroll(random.uniform(0.85, 1.15))
                scrolls += 1
                time.sleep(lognorm(3.4, 0.6, 0.8, 14.0))
        time.sleep(lognorm(1.8, 0.5, 0.4, 6.0))

    else:  # comments
        if imgs > 1 and chance(0.45):
            flip_image(forward=True)
            flipped += 1
            time.sleep(lognorm(2.2, 0.5, 0.7, 7.0))

        depth = comment_depth(energy)
        # 先快速往下甩几次，把评论区拉出来
        lead = min(depth, random.randint(1, 3))
        for _ in range(lead):
            if focus() != "detail":
                break
            detail_scroll(random.uniform(1.15, 1.6))
            scrolls += 1
            time.sleep(lognorm(1.1, 0.45, 0.35, 4.0))

        for i in range(depth - lead):
            if focus() != "detail":
                break
            p = lognorm(0.72, 0.30, 0.35, 1.9)       # 大多慢拖，偶尔用力
            detail_scroll(p)
            scrolls += 1
            # 越往后耐心越差，停留变短
            fade = 1.0 - 0.35 * (i / max(1, depth - lead))
            time.sleep(lognorm(2.6 * fade, 0.52, 0.5, 9.0))
            if chance(0.14):                        # 划过头，拉回来一点
                detail_scroll_back()
                time.sleep(lognorm(1.4, 0.5, 0.4, 4.5))
            if chance(0.05):                        # 停下来再看一会儿
                time.sleep(lognorm(5.5, 0.5, 1.5, 18.0))

    if not like_early:
        do_like()

    lived = time.time() - t_in

    # 退出：有时先往回划一下确认刚才看到哪儿了，再退
    if focus() == "detail" and chance(0.18):
        detail_scroll_back()
        time.sleep(lognorm(1.3, 0.5, 0.4, 4.0))

    target = "search" if source == "search" else "feed"
    for _ in range(3):
        if focus() == target:
            break
        back()
        time.sleep(lognorm(1.3, 0.42, 0.5, 3.2))

    return {"title": title, "style": style, "imgs": imgs, "flipped": flipped,
            "scrolls": scrolls, "liked": liked, "faved": faved,
            "dwell": lived, "mistap": False, "ai": is_ai}


def report(r, stats):
    stats["notes"] += 1
    stats["flipped"] += r["flipped"]
    stats["scrolls"] += r["scrolls"]
    stats["liked"] += 1 if r["liked"] else 0
    stats["faved"] += 1 if r["faved"] else 0
    stats["dwell"].append(r["dwell"])
    if r.get("ai"):
        stats["ai"] += 1
    if r.get("mistap"):
        print(f"  📖 点错了《{r['title'][:20]}》 立刻退回", flush=True)
        return
    tags = [f"停 {r['dwell']:.0f}s"]
    if r["flipped"]:
        tags.append(f"翻图{r['flipped']}")
    if r["scrolls"]:
        tags.append(f"下划{r['scrolls']}")
    if r["liked"]:
        tags.append("♥赞")
    if r["faved"]:
        tags.append("★藏")
    mark = "🤖 " if r.get("ai") else ""
    print(f"  📖 {mark}{STYLE_CN[r['style']]}《{r['title'][:20]}》 图{r['imgs']} · "
          + " ".join(tags), flush=True)


# ================= 行为段 =================
def seg_skim(deadline, seen, args, stats, energy, verbose):
    """
    连着快划，什么都不点。
    这一段里滑动本身也在变：连续甩几下 → 慢一点 → 再甩，不是恒速
    开了主题定向时，快划途中会被相关标题「拽住」停下来看
    """
    n = max(3, int(lognorm(8, 0.5, 3, 26)))
    mood = random.uniform(0.6, 1.4)          # 这一段自己的「节奏感」
    for i in range(n):
        if time.time() > deadline:
            return
        if TOPIC and i > 0 and chance(0.13):
            root = dump_ui()
            if root is not None:
                hot = [t for t, _, _ in feed_notes(root)
                       if hits_topic(t) and t not in seen]
                if hot:
                    r = open_note(seen, args, energy, stats)
                    if r:
                        report(r, stats)
                        mood = random.uniform(0.6, 1.4)
                        continue
        r = random.random()
        if r < 0.55 * mood:
            feed_swipe("flick")
        elif r < 0.90:
            feed_swipe("normal")
        elif r < 0.96:
            feed_swipe("half")               # 滑到一半松手，页面回弹，不算翻页
        else:
            feed_swipe("tiny")               # 手指抖了一下
        if r < 0.90:
            stats["screens"] += 1
        if chance(0.05):
            feed_swipe_back()
        # 停留：多数 0.6-2.4s，偶尔盯着某条标题多看两秒
        t = lognorm(1.3, 0.62, 0.35, 9.0) / mood
        if verbose:
            print(f"    ⚡ 快滑 {i+1}/{n} · 停 {t:.1f}s", flush=True)
        time.sleep(t)


def seg_drift(deadline, seen, args, stats, energy):
    """
    正常节奏。
    先抽一个「滑几屏才愿意点开一篇」的配额，滑够才点，不是每屏掷骰子
    """
    quota = geom(0.42, 0, 9) if chance(0.85) else random.randint(10, 18)
    i = 0
    while time.time() < deadline:
        if i >= quota:
            r = open_note(seen, args, energy, stats)
            if r:
                report(r, stats)
            else:
                feed_swipe("normal")
                stats["screens"] += 1
                time.sleep(lognorm(2.6, 0.5, 0.8, 8.0))
            quota = geom(0.42, 0, 9) if chance(0.85) else random.randint(10, 18)
            i = 0
            if chance(0.30):                     # 看完一篇要缓一下
                time.sleep(lognorm(3.2, 0.6, 0.5, 12.0))
            continue

        feed_swipe(random.choices(["flick", "normal", "half"],
                                  weights=[0.3, 0.6, 0.1])[0])
        stats["screens"] += 1
        i += 1
        if chance(0.08):
            feed_swipe_back()
            time.sleep(lognorm(1.2, 0.5, 0.4, 4.0))
        if chance(0.06):
            feed_swipe("tiny")
        t = lognorm(3.4, 0.60, 0.8, 20.0)
        if args.verbose:
            print(f"    · 滑 {i}/{quota} · 停 {t:.1f}s", flush=True)
        time.sleep(t)


def seg_deep(deadline, seen, args, stats, energy):
    """进入状态：连着看好几篇，中间不滑信息流"""
    n = max(2, int(lognorm(3.0, 0.40, 2, 6)))
    for _ in range(n):
        if time.time() > deadline:
            return
        r = open_note(seen, args, energy, stats)
        if r:
            report(r, stats)
            stagger = lognorm(1.6, 0.6, 0.3, 6.0) * (1.2 - energy * 0.5)
        else:
            feed_swipe("normal")
            stats["screens"] += 1
            stagger = lognorm(2.4, 0.5, 0.6, 7.0)
        time.sleep(stagger)


def seg_rest(deadline, energy):
    """
    停下不滑了。有时真的把手机放下（长时间静止），
    有时只是发呆一下马上又拿起来（短时间）
    """
    if chance(0.30):
        t = lognorm(8.0, 0.45, 2.5, 30.0)       # 短暂放空
    else:
        t = lognorm(45.0, 0.45, 12.0, 150.0)    # 手机放桌上
    print(f"  ☕ 歇 {t:.0f}s（人不看了）", flush=True)
    time.sleep(min(t, max(0, deadline - time.time())))


# ================= 主题定向：搜索通路 =================
def open_search(kw):
    """从信息流进搜索，输入关键词并回车。成功（结果页有卡片）返回 True"""
    if focus() != "search":
        ensure_feed()
        root = dump_ui()
        if root is None:
            return False
        btn = None
        for n in root.iter("node"):
            d = (n.get("content-desc") or "").strip()
            if d == "搜索" and n.get("clickable") == "true":
                b = parse_bounds(n.get("bounds"))
                if b and b[1] < sy(400):          # 顶栏那个，不是输入框里的按钮
                    btn = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                    break
        if btn is None:
            return False
        tap(*btn)
        time.sleep(lognorm(2.0, 0.30, 1.2, 3.6))

    root = dump_ui()
    if root is None:
        return False
    box = None
    for n in root.iter("node"):
        if "EditText" in (n.get("class") or ""):
            b = parse_bounds(n.get("bounds"))
            if b:
                box = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
                break
    if box is None:
        return False

    tap(*box, hesitate=False)
    time.sleep(lognorm(0.9, 0.35, 0.35, 2.2))
    sh("input", "text", kw)
    time.sleep(lognorm(0.7, 0.40, 0.25, 2.0))
    sh("input", "keyevent", "66")               # 回车提交
    time.sleep(lognorm(3.4, 0.35, 2.0, 6.0))

    root = dump_ui()
    return root is not None and bool(search_notes(root))


def back_to_feed(limit=4):
    for _ in range(limit):
        if focus() == "feed":
            return True
        back()
        time.sleep(lognorm(1.4, 0.40, 0.6, 3.2))
    return focus() == "feed"


def seg_topic_search(deadline, seen, args, stats, energy):
    """搜一个主题词，翻结果、点开看，然后回信息流"""
    kw = random.choice(SEARCH_KW)
    if not open_search(kw):
        print("    搜索页没打开，这轮跳过", flush=True)
        back_to_feed()
        return
    print(f"    🔎 搜「{kw}」看结果", flush=True)

    n = max(2, int(lognorm(3.4, 0.45, 2, 8)))
    for i in range(n):
        if time.time() > deadline:
            break
        r = open_note(seen, args, energy, stats, source="search")
        if r:
            report(r, stats)
            time.sleep(lognorm(1.9, 0.50, 0.5, 6.0))
        else:
            feed_swipe("normal")
            stats["screens"] += 1
            time.sleep(lognorm(2.6, 0.50, 0.8, 7.0))
        if i < n - 1 and chance(0.45):           # 往下翻一屏，看更多结果
            feed_swipe("normal")
            stats["screens"] += 1
            time.sleep(lognorm(2.4, 0.50, 0.8, 6.5))

    back_to_feed()


# ================= 主流程 =================
def ensure_feed():
    for _ in range(4):
        st = focus()
        if st == "feed":
            return True
        if st in ("detail", "other", "search"):
            back()
        else:
            sh("monkey", "-p", PKG, "-c",
               "android.intent.category.LAUNCHER", "1")
        time.sleep(1.8)
    return focus() == "feed"


PHASE_CN = {"skim": "快速划过", "drift": "正常浏览", "deep": "沉浸细看",
            "rest": "休息", "topic": "定向浏览"}


def main():
    # Windows 上把输出重定向到文件时控制台编码不是 UTF-8，日志里的 emoji 会直接抛
    # UnicodeEncodeError。这里只放松错误处理（不改编码），控制台里 emoji 照常显示。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="拟人化小红书自动浏览（adb 注入触摸，无需 root）")
    ap.add_argument("-t", "--minutes", type=float, default=20, help="运行时长（分钟），默认 20")
    ap.add_argument("--like", type=float, default=0.03, help="点赞概率，默认 0.03")
    ap.add_argument("--fav", type=float, default=0.01, help="收藏概率，默认 0.01")
    ap.add_argument("--verbose", action="store_true", help="打印每一屏细节")
    ap.add_argument("--classic", action="store_true", help="用 input swipe 替代曲线拖拽")
    ap.add_argument("--dry-run", action="store_true", help="只打印手势参数，不注入事件")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（复现同一场行为）")
    ap.add_argument("-s", "--serial", default=None, help="adb 设备序列号（多设备时指定）")
    ap.add_argument("--adb", default=None, help="adb 可执行文件路径")
    ap.add_argument("--topic", choices=sorted(TOPIC_SETS), default=None,
                    help="主题定向：只点相关内容占多数（目前支持 ai）")
    ap.add_argument("--topic-ratio", type=float, default=0.5,
                    help="主题内容占点开篇数的目标比例，默认 0.5")
    args = ap.parse_args()

    global USE_MOTION, SERIAL, ADB, DRY, LOCAL_UI, TOPIC
    if args.topic:
        TOPIC = TOPIC_SETS[args.topic]
    if args.classic:
        USE_MOTION = False
    if args.dry_run:
        DRY = True
        USE_MOTION = False
    if args.serial:
        SERIAL = args.serial
    ADB = resolve_adb(args.adb)

    # 多设备：没指定 -s 时自动挑选唯一设备；有多台则要求显式指定，否则 adb 会报
    # "more than one device"，每个命令都失败，看起来像是脚本坏了。
    if not SERIAL:
        serials = list_devices()
        if len(serials) > 1:
            print("❌ 检测到多台设备，请用 -s 指定要操作哪一台"
                  "（想同时刷多台，就每台起一个进程）：")
            for sn in serials:
                print(f"   python3 xhs_auto.py -s {sn} -t 20")
            return
        if len(serials) == 1:
            SERIAL = serials[0]
    LOCAL_UI = ui_tmp_path()

    if args.seed is not None:
        random.seed(args.seed)

    w, h = detect_screen()
    if not DRY:
        sh("svc", "power", "stayon", "usb")
    if not ensure_feed():
        print("❌ 没能回到信息流。请确认：手机已解锁、小红书已打开、USB 调试已授权。")
        print("   可先运行 python3 doctor.py 检查环境。")
        return

    deadline = time.time() + args.minutes * 60
    t_start = time.time()
    print(f"设备 {SERIAL or '(默认)'} {w}x{h} · 上限 {args.minutes:g} 分钟 · "
          f"{'曲线触摸' if USE_MOTION else '直线滑动'}"
          f"{' · DRY-RUN' if DRY else ''} · Ctrl+C 停止")
    if TOPIC:
        print(f"定向主题 {args.topic} · 目标占点开篇数 {args.topic_ratio:.0%}"
              f" · 相关内容会看得更久、更容易点赞")
    print()

    seen = set()
    stats = {"screens": 0, "notes": 0, "flipped": 0,
             "scrolls": 0, "liked": 0, "faved": 0, "ai": 0, "dwell": []}
    last = None
    energy = random.uniform(0.30, 0.70)          # 开场精力

    try:
        while time.time() < deadline:
            frac = (time.time() - t_start) / max(1.0, args.minutes * 60)
            # 精力随机游走 + 随时间轻微衰减（越刷越疲）
            energy += random.gauss(0, 0.17) - frac * 0.045
            energy = max(0.05, min(0.95, energy))

            phases = [("skim", 0.30 * (1.65 - energy)),
                      ("drift", 0.45),
                      ("deep", 0.18 * (0.35 + energy * 1.5)),
                      ("rest", 0.02 + 0.07 * (1.7 - energy))]
            if TOPIC:
                # 主题占比落后得越多，越容易安排一次「搜索 + 翻结果」
                deficit = max(0.0, args.topic_ratio * (stats["notes"] + 1) - stats["ai"])
                phases.append(("topic", 0.10 + 1.6 * deficit))
            pool = [(p, w) for p, w in phases if p != last and w > 0] or phases
            phase = random.choices([p[0] for p in pool],
                                   weights=[p[1] for p in pool])[0]
            last = phase
            ensure_feed()
            print(f"▸ {PHASE_CN[phase]}  (精力 {energy:.2f})", flush=True)

            if phase == "rest":
                seg_rest(deadline, energy)
            elif phase == "topic":
                seg_topic_search(deadline, seen, args, stats, energy)
            elif phase == "skim":
                seg_skim(deadline, seen, args, stats, energy, args.verbose)
            elif phase == "deep":
                seg_deep(deadline, seen, args, stats, energy)
            else:
                seg_drift(deadline, seen, args, stats, energy)

            if len(seen) > 300:
                seen.clear()

    except KeyboardInterrupt:
        print("\n⏹ 停止")

    print("\n—— 收工 ——")
    d = stats["dwell"]
    avg = (sum(d) / len(d)) if d else 0
    longest = max(d) if d else 0
    print(f"划过 {stats['screens']} 屏 · 看了 {stats['notes']} 篇 · "
          f"翻图 {stats['flipped']} 张 · 评论下划 {stats['scrolls']} 次 · "
          f"点赞 {stats['liked']} · 收藏 {stats['faved']}")
    if TOPIC and stats["notes"]:
        share = stats["ai"] / stats["notes"]
        print(f"主题命中 {stats['ai']}/{stats['notes']} 篇 "
              f"({share:.0%}，目标 {args.topic_ratio:.0%})")
    print(f"单篇平均停留 {avg:.1f}s · 最长 {longest:.0f}s · "
          f"总时长 {(time.time()-t_start)/60:.1f} 分钟")
    if not DRY:
        sh("svc", "power", "stayon", "false")


if __name__ == "__main__":
    main()
