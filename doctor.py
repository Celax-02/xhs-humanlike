#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs-humanlike 环境自检

在跑 xhs_auto.py 之前先跑这个，它会逐项检查环境并告诉你哪里不通。
只读操作，不会动手机上的任何东西。

用法:
    python3 doctor.py
    python3 doctor.py -s 21982877
    python3 doctor.py --adb ~/Library/Android/sdk/platform-tools/adb
"""

import subprocess, re, sys, argparse, os, shutil
import xml.etree.ElementTree as ET

PKG = "com.xingin.xhs"
BASE_W, BASE_H = 1080, 2340

OK, BAD, WARN = "✔", "✘", "!"

ADB_CANDIDATES = [
    "~/Library/Android/sdk/platform-tools/adb",
    "~/Android/Sdk/platform-tools/adb",
    "~/AppData/Local/Android/Sdk/platform-tools/adb.exe",
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


def run(adb, *args, timeout=25):
    cmd = [adb] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--serial", default=None)
    ap.add_argument("--adb", default=None)
    args = ap.parse_args()

    adb = resolve_adb(args.adb)
    srl = args.serial or os.environ.get("ANDROID_SERIAL")
    bad = 0
    warn = 0

    def good(msg):
        print(f"  {OK} {msg}")

    def fail(msg, hint=""):
        nonlocal bad
        bad += 1
        print(f"  {BAD} {msg}")
        if hint:
            for line in hint.splitlines():
                print(f"     → {line}")

    def soft(msg):
        nonlocal warn
        warn += 1
        print(f"  {WARN} {msg}")

    print("\nxhs-humanlike 环境自检\n" + "─" * 46)

    # 1. adb 本身
    print("\n[1] adb")
    r = run(adb, "version")
    if r is None or r.returncode != 0:
        fail(f"找不到可用的 adb（{adb}）",
             "安装 Android platform-tools：\n"
             "  macOS:  brew install --cask android-platform-tools\n"
             "  Windows/Linux: 从 developer.android.com 下载 platform-tools 解压\n"
             "装好后确认 `adb version` 能跑，或用 --adb 指定完整路径。")
        print("\n自检中止。\n")
        return 1
    good("adb 可用：" + (r.stdout.splitlines()[0] if r.stdout else ""))

    # 2. 设备连接
    print("\n[2] 设备")
    cmd = [adb] + (["-s", srl] if srl else []) + ["devices"]
    r = run(*cmd)
    lines = [l.strip() for l in (r.stdout or "").splitlines()[1:] if l.strip()]
    if not lines:
        fail("没有检测到设备",
             "1) 手机上「开发者选项 → USB 调试」是否打开\n"
             "2) 数据线是否支持数据传输（很多线只能充电）\n"
             "3) 手机弹出的「允许 USB 调试」弹窗是否点了允许\n"
             "4) macOS 若提示「USB 配件需要解锁」，先解锁手机")
        print("\n自检中止。\n")
        return 1
    if len(lines) > 1 and not srl:
        soft(f"检测到多台设备，请用 -s 指定序列号：" +
             ", ".join(l.split()[0] for l in lines))
    state = lines[0].split()[1] if len(lines[0].split()) > 1 else "?"
    if state == "device":
        good("已连接且已授权：" + lines[0])
    elif state == "unauthorized":
        fail("设备未授权（unauthorized）",
             "看手机屏幕，点「允许 USB 调试」。没弹窗就拔插一次数据线。")
    elif state == "offline":
        fail("设备 offline", "执行 `adb kill-server` 后重新插线。")
    else:
        fail(f"设备状态异常：{state}")
    if bad:
        print("\n自检中止。\n")
        return 1

    pre = [adb] + (["-s", srl] if srl else []) + ["shell"]

    def sh(*a, timeout=25):
        return run(*pre, *a, timeout=timeout)

    def prop(k):
        r = sh("getprop", k)
        return (r.stdout or "").strip() if r else ""

    # 3. 系统信息
    print("\n[3] 系统")
    model = prop("ro.product.model") or "未知"
    rel = prop("ro.build.version.release") or "?"
    sdk = prop("ro.build.version.sdk") or "0"
    good(f"{model} · Android {rel} (SDK {sdk})")
    if int(sdk or 0) < 30:
        soft("Android 11 以下也能用，但 motionevent 曲线触摸可能不支持，"
             "建议加 --classic 用直线滑动")

    r = sh("wm", "size")
    m = re.search(r"(\d+)\s*x\s*(\d+)", (r.stdout if r else "") or "")
    w, h = (int(m.group(1)), int(m.group(2))) if m else (BASE_W, BASE_H)
    good(f"分辨率 {w}x{h}（坐标会按 {(w/BASE_W):.2f}x{(h/BASE_H):.2f} 自动缩放）")

    # 4. 小红书
    print("\n[4] 小红书")
    r = sh("pm", "list", "packages", PKG)
    if r and PKG in (r.stdout or ""):
        r2 = sh("dumpsys", "package", PKG)
        m = re.search(r"versionName=(\S+)", r2.stdout or "") if r2 else None
        good(f"已安装 {PKG}" + (f"  v{m.group(1)}" if m else ""))
    else:
        fail("未安装小红书",
             f"从应用商店装好，并在手机上手动打开、登录一次，再跑自检。")
        print("\n自检中止。\n")
        return 1

    # 5. 屏幕状态
    print("\n[5] 屏幕")
    r = sh("dumpsys", "power")
    wake = re.search(r"mWakefulness=(\w+)", r.stdout or "") if r else None
    if wake and wake.group(1) == "Awake":
        good("屏幕已点亮")
    else:
        soft("屏幕当前不是唤醒状态，跑脚本前请先点亮并解锁")
    r = sh("dumpsys", "window")
    keyguard = "isKeyguardShowing=true" in (r.stdout or "") if r else False
    if keyguard:
        soft("设备处于锁屏状态，请先解锁（adb 无法替你解锁）")

    # 6. 界面结构
    print("\n[6] 界面结构")
    r = sh("dumpsys", "window")
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ (\S+?)\}", r.stdout or "") if r else None
    cur = m.group(1) if m else "(读取失败)"
    if "IndexActivityV2" in cur:
        good("小红书在前台，停在信息流")
    elif "NoteDetailActivity" in cur:
        soft("小红书停在帖子详情页，脚本启动时会自己退回信息流")
    else:
        soft(f"当前前台不是小红书（{cur}），脚本会尝试自动拉起")

    sh("uiautomator", "dump", "/sdcard/_doctor.xml", timeout=25)
    run(adb, *(["-s", srl] if srl else []), "pull", "/sdcard/_doctor.xml", "/tmp/_doctor.xml")
    sh("rm", "/sdcard/_doctor.xml")
    cards = []
    try:
        root = ET.parse("/tmp/_doctor.xml").getroot()
        for n in root.iter("node"):
            d = (n.get("content-desc") or "").strip()
            if d.startswith("笔记"):
                cards.append(d)
    except Exception:
        pass
    if cards:
        good(f"无障碍节点树可读，当前屏识别到 {len(cards)} 张笔记卡片")
        sample = cards[0].replace("\n", " ")[:40]
        print(f"     样例：{sample}")
    else:
        fail("读不到笔记卡片",
             "可能原因：\n"
             "1) 没停在小红书「发现」信息流页面\n"
             "2) 小红书版本改了无障碍描述格式（本脚本按 content-desc 解析）\n"
             "3) 系统把 uiautomator dump 禁用了")

    # 7. 输入注入
    print("\n[7] 输入注入能力")
    r = sh("input")
    usage = (r.stderr or "") + (r.stdout or "") if r else ""
    if "motionevent" in usage:
        good("支持 motionevent（曲线触摸，推荐）")
    else:
        soft("不支持 motionevent，请加 --classic 参数改用直线滑动")
    if "swipe" in usage or "tap" in usage:
        good("支持 tap / swipe（基础点击与滑动可用）")
    else:
        fail("input 命令不完整，无法注入触摸事件",
             "这台设备的 shell 权限可能被限制，换一台手机或换官方系统试试。")

    # 结论
    print("\n" + "─" * 46)
    if bad == 0 and warn == 0:
        print(f"{OK} 全部通过，可以直接跑：\n")
    elif bad == 0:
        print(f"{OK} 基本可用（{warn} 项提示），可以先跑：\n")
    else:
        print(f"{BAD} 有 {bad} 项不通，按上面的提示处理完再跑。\n")
    s = f" -s {srl}" if srl else ""
    print(f"  python3 xhs_auto.py{s} -t 20 --like 0.03 --fav 0.01")
    print()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
