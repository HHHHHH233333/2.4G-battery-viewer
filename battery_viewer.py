# -*- coding: utf-8 -*-
"""2.4G 电量查看器：自动识别主流键盘 / 鼠标。

当前内置适配：
  * VGN Neon75（3151:5038）
  * 雷柏 VT9 Pro（24AE:185A）
  * Logitech HID++ 2.0 接收器（046D，含 G304 Lightspeed 20 字节长报文）
  * Razer 雷蛇（1532，90 字节 Feature Report）
  * SteelSeries Rival 3 Wireless（1038，命令 AA 01）
  * ASUS ROG 鼠标（0B05，命令 0712）
  * 标准 HID Battery 设备（部分 Microsoft / Apple / 蓝牙 / 2.4G）

纯 ctypes 读取 HID，无第三方运行时依赖。
"""

from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from ctypes import wintypes

# 必须在导入 tkinter / 创建 Tk 之前设置，否则 Tk 会按 96 DPI 缓存字体。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import messagebox

from hid_win import (
    BATTERY_USAGES,
    HidDevice,
    HidError,
    HidP_Feature,
    HidP_Input,
    caps_blocks,
    enumerate_hid,
    report_descriptor,
)

APP_TITLE = "2.4G 电量"
MUTEX_NAME = "2.4G_Battery_Viewer_SingleInstance"
REFRESH_SECONDS = 15
APP_VERSION = "1.6.0"

# 反馈上报：只在用户主动点“反馈设备”时才联网，平时仍然零网络、零日志。
# 配了 FEEDBACK_ENDPOINT 后“发送给开发者”就是真正的一键上传；
# 没配时回退为“复制报告 + 打开 GitHub Issue 预填页”，同样可用。
FEEDBACK_REPO = "HHHHHH233333/my-repository"
FEEDBACK_ENDPOINT = "https://batteryfeedback-ylbzzgxxdu.cn-hangzhou.fcapp.run"
FEEDBACK_TIMEOUT = 15
FEEDBACK_UA = "2.4G-battery-viewer/" + APP_VERSION

# 更新检测：只在用户主动点“检查更新”时才联网。
# version.json 放在仓库里，version 是最新版本号，download_url 是新 exe 下载地址。
UPDATE_REPO = "HHHHHH233333/2.4G-battery-viewer"
UPDATE_TIMEOUT = 15
UPDATE_DOWNLOAD_TIMEOUT = 600
UPDATE_UA = "2.4G-battery-viewer/" + APP_VERSION

# 颜色：深色科技风，青色霓虹高亮
BG = "#0A0E17"
PANEL = "#121826"
PANEL_LINE = "#1E2838"
SHADOW = "#05080F"
HOVER = "#182132"
BTN_HOVER = "#1B2740"
CARD_LINE = "#1E2A3E"
TEXT = "#E7EEF8"
MUTED = "#8B98AB"
DIM = "#5E6B7F"
GREEN = "#2EE6A8"
AMBER = "#F5B944"
RED = "#FF6B6B"
BLUE = "#4DA6FF"
TRACK = "#1A2233"
ACCENT = "#35C4E8"
ACCENT_MOUSE = "#A78BFA"
BAR_SEGS = 24

FONT = "Microsoft YaHei UI"
FONT_NUM = "Segoe UI Variable Display"
FONT_NUM_LIGHT = "Segoe UI Variable Display Light"
FONT_TEXT = "Segoe UI Variable Text"

# 市面常见键鼠厂商（VID -> 显示名）。没有适配的厂商先显示设备名，
# 再由 --scan / 实测报文逐步加协议。
KNOWN_VID = {
    0x3151: "VGN",
    0x24AE: "雷柏",
    0x046D: "Logitech",
    0x045E: "Microsoft",
    0x05AC: "Apple",
    0x1532: "Razer",
    0x1B1C: "Corsair",
    0x1038: "SteelSeries",
    0x0B05: "ASUS",
    0x0461: "A4Tech",
    0x258A: "狼蛛",
    0x1A2C: "Cherry",
    0x04D9: "Fuhlen",
    0x04F2: "Chicony",
    0x0A5C: "Broadcom",
    0x3837: "MCHOSE",
    0x41E4: "MCHOSE",
}

# 迈从（MCHOSE）键盘型号：官方 M HUB 的 0x3837/0x41E4 设备表里提取。
MCHOSE_MODELS = {
    (0x3837, 0x2020): "MCHOSE K99 V3",
    (0x3837, 0x2015): "MCHOSE G87 V2",
    (0x3837, 0x2023): "MCHOSE K87 V3",
    (0x3837, 0x2021): "MCHOSE G75 V2",
    (0x3837, 0x2025): "MCHOSE G98 V3",
    (0x3837, 0x300A): "MCHOSE Ace 68 Air 2",
    (0x3837, 0x3003): "MCHOSE Ace 68 Pro",
    (0x3837, 0x301D): "MCHOSE Ace 75",
    (0x41E4, 0x2101): "MCHOSE Ace 60",
    (0x41E4, 0x2103): "MCHOSE Ace 60 Pro",
    (0x41E4, 0x2112): "MCHOSE Ace 60",
    (0x41E4, 0x2114): "MCHOSE Ace 68",
    (0x41E4, 0x2116): "MCHOSE Ace 68 Pro",
    (0x41E4, 0x2118): "MCHOSE Jet 75",
    (0x41E4, 0x211A): "MCHOSE Jet 75",
    (0x41E4, 0x211C): "MCHOSE Zero 75",
    (0x41E4, 0x2120): "MCHOSE Ace 68 Air",
    (0x41E4, 0x2122): "MCHOSE Mix 87",
    (0x41E4, 0x2126): "MCHOSE Ace 60",
    (0x41E4, 0x2132): "MCHOSE Ace 68 Air",
}

# Logitech HID++ 2.0 feature id（来自 Solaar / Logitech 公开文档）
LOGI_BATTERY_STATUS = 0x1000
LOGI_BATTERY_VOLTAGE = 0x1001
LOGI_UNIFIED_BATTERY = 0x1004
LOGI_DEVICE_NAME = 0x0005

# 迈从 M HUB 的 HID 报文：report id 77，7 字节头 + 数据 + 校验。
MCHOSE_REPORT_ID = 0x4D
MCHOSE_HEADER_LEN = 7
MCHOSE_CMD_BATTERY = 0x0904
_mchose_seq = 0

# 雷蛇（Razer）HID 报告：90 字节 Feature Report，命令类/命令号 + CRC。
RAZER_VID = 0x1532
RAZER_TXN_IDS = (0x1F, 0x3F, 0xFF, 0x00)
RAZER_CMD_BATTERY = 0x80
RAZER_CMD_CHARGING = 0x84
_razer_cache: dict = {}
_razer_lock = threading.Lock()

# SteelSeries Rival 3 Wireless 等：64 字节 Output Report，命令 AA 01。
STEELSERIES_VID = 0x1038
STEELSERIES_PIDS = {0x1830, 0x1832, 0x1834, 0x1836}
STEELSERIES_MODELS = {
    0x1830: "SteelSeries Rival 3 Wireless",
    0x1832: "SteelSeries 无线鼠标",
    0x1834: "SteelSeries 无线鼠标",
    0x1836: "SteelSeries 无线鼠标",
}
_ss_cache: dict = {}
_ss_lock = threading.Lock()

# ASUS ROG 鼠标：65 字节 Output Report，命令 0x0712，回报第 5 字节电量。
ASUS_VID = 0x0B05
ASUS_PID_RECEIVER = 0x1A68
ASUS_PID_BLUETOOTH = 0x1A66
ASUS_ROG_WORDS = ("ROG", "KERIS", "GLADIUS", "HARPE", "CHAKRAM",
                  "SPATHA", "PUGIO", "STRIX", "AIMPOINT", "TUF")
_asus_cache: dict = {}
_asus_lock = threading.Lock()

# HID++ 请求里软件 id 固定用 8，方便把“请求回复”和“通知”区分开。
LOGI_SW_ID = 0x08

# 罗技按接口缓存探测结果，避免每 3 秒都把接收器全查一遍。
_logi_cache: dict = {}
_logi_lock = threading.Lock()


def vendor_name(vid: int) -> str:
    return KNOWN_VID.get(vid, "")


@dataclass
class DeviceState:
    online: bool = False
    battery: int | None = None
    charging: bool = False
    status: str = "正在检测"
    detail: str = ""
    updated: float | None = None
    name: str = ""


def _elide(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def level_color(pct: int | None) -> str:
    if pct is None:
        return DIM
    if pct <= 20:
        return RED
    if pct <= 50:
        return AMBER
    return GREEN


def _mix_hex(a: str, b: str, t: float) -> str:
    """两个 #RRGGBB 颜色按 t(0~1) 线性插值。"""
    a = a.lstrip("#"); b = b.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    bl = int(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _bar_gradient(base: str, t: float) -> str:
    """进度条霓虹光晕：左端偏暗、右端更亮。"""
    dim = _mix_hex(base, BG, 0.45)
    bright = _mix_hex(base, "#FFFFFF", 0.30)
    return _mix_hex(dim, bright, t)


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """圆角矩形，返回 polygon id。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kwargs)




# ---------------------------------------------------------------------------
# 通用适配器：标准 HID 电池 / Logitech HID++ / 未适配设备
# ---------------------------------------------------------------------------

STANDARD_BATTERY_USAGES = {
    (page, usage) for page, usage, _desc in BATTERY_USAGES
    if usage != 0x0029  # RF Signal Strength 不是电量
}


def _battery_usage(caps) -> tuple[int, int] | None:
    if getattr(caps, "IsRange", 0):
        return None
    try:
        return caps.UsagePage, caps.u.NotRange.Usage
    except Exception:
        return None


def _group_kinds(ifaces) -> set[str]:
    """根据同一个物理设备里的 HID 顶层集合判断键盘/鼠标。"""
    kinds = set()
    for d in ifaces:
        if d.usage_page == 0x0001 and d.usage in (0x0006, 0x0007):
            kinds.add("keyboard")
        elif d.usage_page == 0x0001 and d.usage in (0x0001, 0x0002):
            kinds.add("mouse")
    return kinds


def _display_name(d, fallback: str) -> str:
    product = (d.product or "").strip()
    vendor = vendor_name(d.vid)
    if product and product.lower() not in ("usb receiver", "receiver", "hid-compliant device"):
        return product
    if vendor:
        return f"{vendor} {fallback}"
    return f"{d.vid:04X}:{d.pid:04X} {fallback}"


def _scale_battery(value: int, caps) -> int | None:
    try:
        lo, hi = int(caps.LogicalMin), int(caps.LogicalMax)
    except Exception:
        lo, hi = 0, 100
    if value is None:
        return None
    if 0 <= value <= 100:
        return value
    if hi > lo:
        pct = round((value - lo) * 100.0 / (hi - lo))
        if 0 <= pct <= 100:
            return int(pct)
    return None


def _grouped_hid():
    groups: dict = {}
    try:
        devices = enumerate_hid()
    except Exception:
        return {}
    for d in devices:
        if getattr(d, "error", ""):
            continue
        key = getattr(d, "group", "") or d.path
        groups.setdefault(key, []).append(d)
    return groups


def read_standard_battery(kind: str) -> DeviceState | None:
    """读取遵循 HID Battery 标准 usage 的设备。

    覆盖部分 Microsoft / Apple / 蓝牙 / 2.4G 键鼠。只读 Feature Report，
    不向设备写任何东西。
    """
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            if not d.feature_len:
                continue
            try:
                with HidDevice(d.path, write=False) as dev:
                    for caps in dev.value_caps(HidP_Feature):
                        if _battery_usage(caps) not in STANDARD_BATTERY_USAGES:
                            continue
                        raw = dev.get_feature_report(caps.ReportID, d.feature_len)
                        value = dev.usage_value(HidP_Feature, raw, caps)
                        pct = _scale_battery(value, caps)
                        if pct is None:
                            continue
                        name = _display_name(d, "键盘" if kind == "keyboard" else "鼠标")
                        return DeviceState(
                            True, pct, False, "2.4G 已连接",
                            f"{vendor_name(d.vid) or '标准 HID'} · "
                            f"{d.vid:04X}:{d.pid:04X} · 标准 HID 电池",
                            time.time(), name,
                        )
            except Exception:
                continue
    return None


def _guess_kind(name: str) -> str | None:
    low = (name or "").lower()
    keyboard_words = ("keyboard", "keypad", "keys", "k380", "k580", "k780",
                      "mx keys", "craft", "wave", "键盘")
    mouse_words = ("mouse", "trackball", "mx master", "mx anywhere", "pebble",
                   "g502", "g304", "g pro", "m185", "m235", "m330", "m590",
                   "m720", "lift", "鼠标", "轨迹球")
    if any(w in low for w in keyboard_words):
        return "keyboard"
    if any(w in low for w in mouse_words):
        return "mouse"
    return None


def _hidpp_send(dev: HidDevice, devnum: int, req_id: int, params: bytes = b"",
                timeout_ms: int = 520, long: bool = False) -> bytes | None:
    """发一个 HID++ 2.0 报文，返回去掉 4 字节头之后的 payload。

    Lightspeed 接收器（如 046D:C53F）只有长报文 0x11 能通，短报文 0x10 会被拒绝；
    Unifying 等老接收器则用 7 字节短报文。
    """
    rid = 0x11 if long else 0x10
    frame = bytes([rid, devnum & 0xFF,
                   (req_id >> 8) & 0xFF, req_id & 0xFF]) + bytes(params)
    size = 20 if long else max(
        int(getattr(dev.caps, "OutputReportByteLength", 0) or 0), len(frame), 7)
    frame = frame + b"\x00" * (size - len(frame))
    try:
        dev.drain()
        try:
            dev.write_output(frame, use_hidd=True)
        except Exception:
            dev.write_output(frame, use_hidd=False)
        expected_index = (req_id >> 8) & 0xFF
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
            resp = dev.read_input(timeout_ms=int(remain * 1000) or 1)
            if not resp or len(resp) < 4:
                return None
            if resp[0] != rid:
                continue
            if resp[1] not in (devnum & 0xFF, (devnum & 0xFF) ^ 0xFF):
                continue
            # HID++ 错误帧是 [.., 0xFF, req_hi, req_lo, error]，不能当成功回复解析。
            if resp[2] == 0xFF:
                return None
            # 鼠标移动/按键通知也会走 0x11；feature 对不上就继续等真正的回复。
            if resp[2] != expected_index or (resp[3] & 0x0F) != LOGI_SW_ID:
                continue
            break
    except Exception:
        return None
    return resp[4:]


def _hidpp_feature_index(dev: HidDevice, devnum: int, feature: int,
                         long: bool = False) -> int | None:
    payload = _hidpp_send(dev, devnum, LOGI_SW_ID,
                          bytes([(feature >> 8) & 0xFF, feature & 0xFF, 0x00]),
                          long=long)
    if not payload:
        return None
    index = payload[0]
    return index or None


def _hidpp_request(dev: HidDevice, devnum: int, feature: int,
                   function: int = 0x00, params: bytes = b"",
                   long: bool = False) -> bytes | None:
    index = _hidpp_feature_index(dev, devnum, feature, long=long)
    if not index:
        return None
    req_id = (index << 8) | ((function & 0xF0) | LOGI_SW_ID)
    payload = _hidpp_send(dev, devnum, req_id, params, long=long)
    if not payload:
        return None
    if len(payload) >= 2 and payload[0] == 0xFF:
        return None
    return payload


def _hidpp_name(dev: HidDevice, devnum: int, long: bool = False) -> str:
    # 0x0005 的 get_name 是 function 1（0x10）；function 0 只返回名字长度。
    payload = _hidpp_request(dev, devnum, LOGI_DEVICE_NAME, 0x10, long=long)
    if not payload:
        return ""
    text = payload.decode("utf-8", "ignore").split("\x00", 1)[0].strip()
    if text:
        return text
    # 个别老固件按 [len, chars...] 返回
    n = payload[0]
    if 0 < n <= len(payload) - 1:
        try:
            return payload[1:1 + n].decode("utf-8", "ignore").strip("\x00 ")
        except Exception:
            return ""
    return ""


def _logi_decode_battery(feature: int, payload: bytes):
    """返回 (百分比, 是否充电)；解析失败返回 (None, False)。"""
    try:
        if feature == LOGI_BATTERY_STATUS:
            if len(payload) < 3:
                return None, False
            level, _next, status = payload[0], payload[1], payload[2]
            if not (1 <= level <= 100):
                return None, False
            # 0/1/2/4 在不同固件里分别表示放电/充电/满/慢充；
            # 这里只在明确不是“放电”时标充电，避免满电时误显示。
            return level, status not in (0, 3, 5, 6, 7)
        if feature == LOGI_BATTERY_VOLTAGE:
            if len(payload) < 3:
                return None, False
            mv = (payload[0] << 8) | payload[1]
            flags = payload[2]
            # 3.5V 空 ~ 4.2V 满，仅用于没有百分比字段的旧设备
            pct = round((mv - 3500) * 100.0 / 700.0)
            pct = max(0, min(100, int(pct)))
            if pct <= 0:
                return None, False
            return pct, bool(flags & 0x80)
        if feature == LOGI_UNIFIED_BATTERY:
            if len(payload) < 3:
                return None, False
            discharge, approx, status = payload[0], payload[1], payload[2]
            if 1 <= discharge <= 100:
                pct = discharge
            else:
                pct = {8: 100, 4: 75, 2: 30, 1: 10}.get(approx, 0)
            if pct <= 0:
                return None, False
            return pct, status not in (0, 3, 5, 6, 7)
    except Exception:
        pass
    return None, False


def _logi_probe(dev: HidDevice, devnum: int, kind: str,
                long: bool = False) -> DeviceState | None:
    name = _hidpp_name(dev, devnum, long=long)
    if "receiver" in name.lower() or "接收器" in name:
        return None
    name_kind = _guess_kind(name)
    if name and name_kind and name_kind != kind:
        return None
    for feature in (LOGI_BATTERY_STATUS, LOGI_BATTERY_VOLTAGE, LOGI_UNIFIED_BATTERY):
        payload = _hidpp_request(dev, devnum, feature, long=long)
        if not payload:
            continue
        pct, charging = _logi_decode_battery(feature, payload)
        if pct is None:
            continue
        display = name or ("Logitech 键盘" if kind == "keyboard" else "Logitech 鼠标")
        return DeviceState(
            True, pct, charging, "充电中" if charging else "2.4G 已连接",
            f"Logitech · HID++ 2.0 {'长报文' if long else '短报文'}",
            time.time(), display,
        )
    return None


def read_logitech_battery(kind: str) -> DeviceState | None:
    """读取罗技 HID++ 2.0 接收器/直连设备。

    这是“最佳努力”实现：罗技接收器要先按设备号 1/2/0xFF 探测，
    不同型号回复速度不一样；没有罗技硬件时不会影响现有设备。
    """
    long_ifaces = []
    short_ifaces = []
    for d in _grouped_hid().values():
        for item in d:
            if item.vid != 0x046D or item.usage_page != 0xFF00:
                continue
            if item.input_len == 20 and item.output_len >= 20:
                long_ifaces.append(item)
            elif item.input_len == 7 and item.output_len >= 7:
                short_ifaces.append(item)
    # Lightspeed 接收器只认 0x11 长报文；Unifying 等老接收器用 0x10 短报文。
    for long, ifaces in ((True, long_ifaces), (False, short_ifaces)):
        for d in ifaces:
            key = (d.path, kind, long)
            now = time.monotonic()
            with _logi_lock:
                cached = _logi_cache.get(key)
            if cached:
                ts, state = cached
                if state is not None and now - ts < 10:
                    return DeviceState(**state.__dict__)
                if state is None and now - ts < 60:
                    continue
            state = None
            receiver = "receiver" in (d.product or "").lower() or "接收器" in (d.product or "")
            devnums = (1, 2, 0xFF, 0) if receiver else (0xFF, 0, 1, 2)
            try:
                with HidDevice(d.path, write=True) as dev:
                    if not dev.write_access:
                        continue
                    for devnum in devnums:
                        try:
                            state = _logi_probe(dev, devnum, kind, long=long)
                        except Exception:
                            state = None
                        if state is not None:
                            break
            except Exception:
                state = None
            with _logi_lock:
                _logi_cache[key] = (time.monotonic(), state)
            if state is not None:
                return state
    return None







def read_asus_battery(kind: str) -> DeviceState | None:
    """ASUS ROG 鼠标：命令 0x0712，同步 65 字节 Output/Input Report。"""
    if kind != "mouse":
        return None
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            if d.vid != ASUS_VID or d.output_len < 65:
                continue
            name = (d.product or "").strip()
            upper = name.upper()
            if d.pid not in (ASUS_PID_RECEIVER, ASUS_PID_BLUETOOTH) and not any(
                    w in upper for w in ASUS_ROG_WORDS):
                continue
            key = (d.path, kind)
            now = time.monotonic()
            with _asus_lock:
                cached = _asus_cache.get(key)
            if cached:
                ts, state = cached
                if state is not None and now - ts < 5:
                    return DeviceState(**state.__dict__)
                if state is None and now - ts < 30:
                    return None
            state = None
            try:
                with HidDevice(d.path, write=True) as dev:
                    if not dev.write_access:
                        continue
                    frame = b"\x00\x12\x07" + b"\x00" * 62
                    if d.output_len > len(frame):
                        frame += b"\x00" * (d.output_len - len(frame))
                    dev.write_output(frame, use_hidd=False)
                    resp = dev.read_input_sync(timeout_ms=600,
                                               length=d.input_len or 65)
                    if resp and len(resp) >= 7:
                        if resp[1] == 0x12 and resp[2] == 0x07:
                            off = 1
                        elif resp[0] == 0x12 and resp[1] == 0x07:
                            off = 0
                        else:
                            off = -1
                        if off >= 0 and len(resp) >= off + 6:
                            pct = resp[off + 4]
                            charge = resp[off + 5]
                            if 0 <= pct <= 100 and not (pct == 0 and charge == 0):
                                charging = charge == 0x01
                                wireless = d.pid in (ASUS_PID_RECEIVER,
                                                     ASUS_PID_BLUETOOTH)
                                state = DeviceState(
                                    True, pct, charging,
                                    "充电中" if charging else
                                    ("2.4G 已连接" if wireless else "USB 已连接"),
                                    f"ASUS ROG · {d.vid:04X}:{d.pid:04X}",
                                    time.time(),
                                    name or "ASUS ROG 鼠标",
                                )
            except Exception:
                state = None
            with _asus_lock:
                _asus_cache[key] = (time.monotonic(), state)
            if state is not None:
                return state
    return None


def _ss_parse(resp: bytes) -> tuple[int, bool] | None:
    if not resp:
        return None
    data = resp
    # Windows 的输入报告通常带 report id 0；Linux 抓包里没有。两种都兼容。
    if len(data) >= 65 and data[0] == 0:
        data = data[1:]
    for off in (0, 1):
        if len(data) < off + 2:
            continue
        level = data[off] | (data[off + 1] << 8)
        if 0 <= level <= 100:
            charging = bool(data[off + 2] & 0x01) if len(data) >= off + 3 else False
            return level, charging
    return None


def read_steelseries_battery(kind: str) -> DeviceState | None:
    """SteelSeries Rival 3 Wireless 等：命令 AA 01，uint16 LE 电量。"""
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            if d.vid != STEELSERIES_VID or d.pid not in STEELSERIES_PIDS:
                continue
            if d.output_len < 64:
                continue
            key = (d.path, kind)
            now = time.monotonic()
            with _ss_lock:
                cached = _ss_cache.get(key)
            if cached:
                ts, state = cached
                if state is not None and now - ts < 5:
                    return DeviceState(**state.__dict__)
                if state is None and now - ts < 30:
                    return None
            state = None
            try:
                with HidDevice(d.path, write=True) as dev:
                    if not dev.write_access:
                        continue
                    cmd = bytes([0xAA, 0x01]) + b"\x00" * 62
                    if d.output_len >= 65:
                        frame = b"\x00" + cmd
                    else:
                        frame = cmd
                    if len(frame) < d.output_len:
                        frame += b"\x00" * (d.output_len - len(frame))
                    try:
                        dev.write_output(frame, use_hidd=True)
                    except Exception:
                        dev.write_output(frame, use_hidd=False)
                    parsed = None
                    for _ in range(3):
                        resp = dev.read_input(timeout_ms=450)
                        parsed = _ss_parse(resp) if resp else None
                        if parsed is not None:
                            break
                        time.sleep(0.03)
                    if parsed is not None:
                        level, charging = parsed
                        name = (d.product or "").strip()
                        if not name or "steelseries" not in name.lower():
                            name = STEELSERIES_MODELS.get(d.pid, "SteelSeries 鼠标")
                        state = DeviceState(
                            True, level, charging,
                            "充电中" if charging else "2.4G 已连接",
                            f"SteelSeries · {d.vid:04X}:{d.pid:04X} · 2.4G",
                            time.time(), name,
                        )
            except Exception:
                state = None
            with _ss_lock:
                _ss_cache[key] = (time.monotonic(), state)
            if state is not None:
                return state
    return None


def _razer_report(command_class: int, command_id: int, data_size: int,
                  transaction_id: int) -> bytes:
    report = bytearray(90)
    report[0] = 0x00
    report[1] = transaction_id & 0xFF
    report[2:4] = b"\x00\x00"
    report[4] = 0x00
    report[5] = data_size & 0xFF
    report[6] = command_class & 0xFF
    report[7] = command_id & 0xFF
    crc = 0
    for b in report[2:88]:
        crc ^= b
    report[88] = crc
    report[89] = 0x00
    return bytes(report)


def _razer_send(dev: HidDevice, command_class: int, command_id: int,
                transaction_id: int) -> bytes | None:
    try:
        feature_len = int(getattr(dev.caps, "FeatureReportByteLength", 0) or 0)
        if feature_len < 90:
            return None
        request = _razer_report(command_class, command_id, 2, transaction_id)
        if feature_len > len(request):
            request = request + b"\x00" * (feature_len - len(request))
        dev.set_feature_report(request)
        # 设备需要一点时间把回复写进 Feature Report；Windows 上读得太快会拿到旧数据。
        time.sleep(0.30)
        response = dev.get_feature_report(0, feature_len)
        if not response or len(response) < 10:
            return None
        if response[6] != (command_class & 0xFF) or response[7] != (command_id & 0xFF):
            return None
        if response[0] not in (0x01, 0x02):
            return None
        return response
    except Exception:
        return None


def _razer_read_device(dev: HidDevice, d, kind: str) -> DeviceState | None:
    for transaction_id in RAZER_TXN_IDS:
        response = _razer_send(dev, 0x07, RAZER_CMD_BATTERY, transaction_id)
        if response is None:
            continue
        raw = response[9]
        if raw > 255:
            continue
        pct = int(round(raw * 100.0 / 255.0))
        charging = False
        charge = _razer_send(dev, 0x07, RAZER_CMD_CHARGING, transaction_id)
        if charge is not None and charge[9] == 1:
            charging = True
        name = (d.product or "").strip()
        if not name or "razer" not in name.lower():
            name = "Razer 键盘" if kind == "keyboard" else "Razer 鼠标"
        return DeviceState(
            True, pct, charging, "充电中" if charging else "2.4G 已连接",
            f"Razer · HID · {d.vid:04X}:{d.pid:04X}",
            time.time(), name,
        )
    return None


def read_razer_battery(kind: str) -> DeviceState | None:
    """雷蛇 Razer：90 字节 Feature Report，命令类 0x07 / 电量 0x80。"""
    candidates = []
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            if d.vid == RAZER_VID and d.feature_len >= 90:
                candidates.append(d)
    # 先试厂商页接口，再试其它接口
    candidates.sort(key=lambda d: 0 if d.usage_page >= 0xFF00 else 1)
    for d in candidates:
        key = (d.path, kind)
        now = time.monotonic()
        with _razer_lock:
            cached = _razer_cache.get(key)
        if cached:
            ts, state = cached
            if state is not None and now - ts < 8:
                return DeviceState(**state.__dict__)
            if state is None and now - ts < 45:
                return None
        state = None
        try:
            with HidDevice(d.path, write=True) as dev:
                if dev.write_access:
                    state = _razer_read_device(dev, d, kind)
        except Exception:
            state = None
        with _razer_lock:
            _razer_cache[key] = (time.monotonic(), state)
        if state is not None:
            return state
    return None


def _mchose_model(d) -> str:
    return (MCHOSE_MODELS.get((d.vid, d.pid))
            or (d.product or "").strip()
            or "MCHOSE 键盘")


def _mchose_packet(command: int, data: bytes = b"", seq: int = 0) -> bytes:
    """构造 M HUB 的 31 字节报文（不含 report id）。"""
    header = bytes([
        1, 3, len(data) & 0xFF,
        (command >> 8) & 0xFF, command & 0xFF,
        0, seq & 0xFF,
    ])
    body = header + bytes(data)
    check = 0
    for b in body[1:]:
        check ^= b
    packet = body + bytes([check])
    return packet + b"\x00" * max(0, 31 - len(packet))


def _mchose_parse(payload: bytes):
    """解析 M HUB 回复，返回 (电量, 充电状态)；不是电池回复则返回 None。"""
    if len(payload) < 8 or payload[0] != 1:
        return None
    data_len = payload[2]
    command = (payload[3] << 8) | payload[4]
    if command != MCHOSE_CMD_BATTERY or data_len < 2:
        return None
    data = payload[7:7 + data_len]
    if len(data) < 2:
        return None
    charge_status, level = data[0], data[1]
    if not (0 <= level <= 100):
        return None
    return level, charge_status == 1


def read_mchose_battery(kind: str) -> DeviceState | None:
    """迈从（MCHOSE）键盘，M HUB 协议：report 77 + 命令 0x0904。"""
    global _mchose_seq
    if kind != "keyboard":
        return None
    for ifaces in _grouped_hid().values():
        if "keyboard" not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            product = (d.product or "") + " " + (d.manufacturer or "")
            is_mchose = (d.vid in (0x3837, 0x41E4)
                         or "mchose" in product.lower()
                         or "迈从" in product)
            if not is_mchose or d.output_len < 8:
                continue
            try:
                with HidDevice(d.path, write=True) as dev:
                    if not dev.write_access:
                        continue
                    seq = _mchose_seq & 0xFF
                    _mchose_seq = (_mchose_seq + 1) & 0xFF
                    packet = _mchose_packet(MCHOSE_CMD_BATTERY, b"", seq)
                    frame = bytes([MCHOSE_REPORT_ID]) + packet
                    size = max(d.output_len, len(frame))
                    frame = frame + b"\x00" * (size - len(frame))
                    dev.drain()
                    try:
                        dev.write_output(frame, use_hidd=True)
                    except Exception:
                        dev.write_output(frame, use_hidd=False)
                    for _ in range(3):
                        resp = dev.read_input(timeout_ms=600)
                        if not resp:
                            continue
                        payload = resp[1:] if resp[0] == MCHOSE_REPORT_ID else resp
                        parsed = _mchose_parse(payload)
                        if parsed is None:
                            continue
                        pct, charging = parsed
                        wireless = (d.pid == 0x3033
                                    or "2.4g" in (d.product or "").lower())
                        link = "2.4G" if wireless else "USB"
                        return DeviceState(
                            True, pct, charging,
                            "充电中" if charging else "2.4G 已连接",
                            f"MCHOSE · {d.vid:04X}:{d.pid:04X} · {link}",
                            time.time(), _mchose_model(d),
                        )
            except Exception:
                continue
    return None


def receiver_offline(kind: str) -> DeviceState | None:
    """接收器还在，但设备没有应答：显示“未响应”，而不是“未检测到接收器”。"""
    fallback = "键盘" if kind == "keyboard" else "鼠标"
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            product = (d.product or "").strip()
            low = product.lower()
            is_receiver = ("receiver" in low or "接收器" in product
                           or (d.vid == 0x24AE and d.pid == 0x185A))
            if not is_receiver:
                continue
            vendor = vendor_name(d.vid) or "未知厂商"
            return DeviceState(
                False, None, False, f"{fallback}未响应",
                f"{vendor} · 接收器在线，{fallback}未响应",
                None, f"{vendor} {fallback}",
            )
    return None


def read_unsupported(kind: str) -> DeviceState | None:
    """已识别到键鼠，但还没有对应协议：显示设备名，不谎报电量。"""
    fallback = "键盘" if kind == "keyboard" else "鼠标"
    for ifaces in _grouped_hid().values():
        if kind not in _group_kinds(ifaces):
            continue
        for d in ifaces:
            product = (d.product or "").strip()
            low = product.lower()
            if "receiver" in low or "接收器" in product or not product:
                continue
            name = _display_name(d, fallback)
            return DeviceState(
                False, None, False, "已识别 · 暂不支持电量",
                f"{vendor_name(d.vid) or '未知厂商'} · {d.vid:04X}:{d.pid:04X}",
                None, name,
            )
    return None



class Monitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._kb = DeviceState()
        self._mouse = DeviceState()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def snapshot(self):
        with self._lock:
            return (
                DeviceState(**self._kb.__dict__),
                DeviceState(**self._mouse.__dict__),
            )

    def _publish(self, kb=None, mouse=None):
        with self._lock:
            if kb is not None:
                self._kb = kb
            if mouse is not None:
                self._mouse = mouse

    # ---- 键盘 ----
    def _read_keyboard(self) -> DeviceState:
        target = None
        try:
            for d in enumerate_hid():
                if (d.vid == 0x3151 and d.feature_len == 65
                        and d.usage_page == 0xFFFF and d.usage == 0x0002):
                    target = d
                    break
        except Exception:
            target = None
        if target is None:
            for reader in (read_mchose_battery, read_razer_battery,
                           read_steelseries_battery, read_logitech_battery,
                           read_standard_battery):
                state = reader("keyboard")
                if state is not None:
                    return state
            return (receiver_offline("keyboard") or read_unsupported("keyboard")
                    or DeviceState(
                        False, None, False, "未检测到接收器",
                        "自动识别 · 2.4G / 蓝牙 / USB",
                        None, "自动识别键盘"))

        try:
            with HidDevice(target.path, write=True) as dev:
                # 0x3151 同族接收器（VGN/ATK/AJAZZ 共用）：Feature Report 不会主动
                # 更新，必须先 SET_FEATURE 发 0xF7 状态轮询让 2.4G 遥测链路上线，
                # 之后 GET_FEATURE 才能读到电量（否则一直是全 0）。
                poll = bytearray(65)
                poll[0] = 0x00          # report id
                poll[1] = 0xF7          # 状态轮询 opcode
                raw = b""
                for _ in range(3):
                    dev.set_feature_report(bytes(poll))
                    time.sleep(0.03)
                    raw = dev.get_feature_report(0, 65)
                    if len(raw) >= 8 and raw[2] != 0:
                        break
        except (HidError, OSError, Exception):
            return DeviceState(False, None, False, "读取失败",
                               "VGN Neon75 · 3151:5038",
                               None, "VGN 霓虹 Neon75")

        if len(raw) < 11:
            return DeviceState(False, None, False, "数据异常",
                               "VGN Neon75 · 3151:5038",
                               None, "VGN 霓虹 Neon75")
        pct = raw[2]
        if not (0 <= pct <= 100):
            return DeviceState(False, None, False, "数据异常",
                               "VGN Neon75 · 3151:5038",
                               None, "VGN 霓虹 Neon75")
        # 全 0 = 2.4G 遥测链路没上线（键盘休眠 / 没连上），不是真的 0%
        if pct == 0 and not any(raw[i] for i in (4, 5, 6, 7)):
            return DeviceState(False, None, False, "未连接",
                               "VGN Neon75 · 3151:5038",
                               None, "VGN 霓虹 Neon75")
        # 2.4G 下不猜充电状态（报告里没有可靠的充电标志）
        status = "2.4G 已连接"
        return DeviceState(True, pct, False, status,
                           f"VGN Neon75 · {target.vid:04X}:{target.pid:04X}",
                           time.time(), "VGN 霓虹 Neon75")

    # ---- 鼠标 ----
    def _find_mouse(self):
        try:
            for d in enumerate_hid():
                if (d.vid == 0x24AE and d.pid == 0x185A
                        and d.usage_page == 0xFF00 and d.usage == 0x0002
                        and d.input_len == 7):
                    return d
        except Exception:
            return None
        return None

    def _run(self):
        kb_next = 0.0
        mouse_dev = None
        mouse_last = 0.0
        mouse_probe_deadline = 0.0
        rapoo_cooldown = 0.0

        mouse_poll_next = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if self._wake.is_set():
                self._wake.clear()
                kb_next = 0.0
                mouse_poll_next = 0.0
                rapoo_cooldown = 0.0
                mouse_probe_deadline = 0.0
                with _logi_lock:
                    _logi_cache.clear()
                if mouse_dev is not None:
                    try:
                        mouse_dev.close()
                    except Exception:
                        pass
                    mouse_dev = None

            # 键盘每 15 秒读一次；手动刷新立即读
            if now >= kb_next:
                self._publish(kb=self._read_keyboard())
                kb_next = now + REFRESH_SECONDS

            # 雷柏 VT9 Pro 走接收器周期广播；其它品牌走轮询。
            # 注意：HID 枚举一次约十几毫秒，不能每 50ms 跑一遍，否则白烧 CPU。
            if mouse_dev is None and now >= mouse_poll_next:
                # 雷柏接收器在但鼠标睡着时，不要一直霸占；冷却一段时间让别的鼠标接管。
                target = self._find_mouse() if now >= rapoo_cooldown else None
                if target is not None:
                    try:
                        mouse_dev = HidDevice(target.path, write=False)
                        mouse_dev.drain()
                        mouse_last = time.monotonic()
                        # 雷柏接收器约 3 秒一包，首包最多要等一个周期，留 4 秒。
                        mouse_probe_deadline = time.monotonic() + 4.0
                    except Exception:
                        mouse_dev = None
                if mouse_dev is None:
                    state = None
                    for reader in (read_razer_battery, read_steelseries_battery,
                                   read_asus_battery, read_logitech_battery,
                                   read_standard_battery):
                        try:
                            state = reader("mouse")
                        except Exception:
                            state = None
                        if state is not None:
                            break
                    if state is None:
                        state = (receiver_offline("mouse")
                                 or read_unsupported("mouse")
                                 or DeviceState(
                                     False, None, False, "未检测到接收器",
                                     "自动识别 · 2.4G / 蓝牙 / USB",
                                     None, "自动识别鼠标"))
                    self._publish(mouse=state)
                mouse_poll_next = now + 3.0

            if mouse_dev is not None:
                try:
                    data = mouse_dev.read_input(timeout_ms=250)
                except Exception:
                    data = None
                    try:
                        mouse_dev.close()
                    except Exception:
                        pass
                    mouse_dev = None
                    rapoo_cooldown = time.monotonic() + 30.0
                    mouse_poll_next = 0.0

                if data:
                    if len(data) >= 7 and data[0] == 0xBB and 0 <= data[6] <= 100:
                        pct = data[6]
                        # VT9 Pro 的 7 字节状态包没有可靠的充电标志：
                        # 实测无线时 b[5]=0x02，插线充电时 b[5] 仍是 0x02。
                        # 所以无线/插线充电统一显示“2.4G 已连接”，不猜充电。
                        self._publish(mouse=DeviceState(
                            True, pct, False, "2.4G 已连接",
                            "Rapoo VT9 Pro · 24AE:185A", time.time(),
                            "雷柏 VT9 Pro"))
                        mouse_last = time.monotonic()
                        mouse_probe_deadline = 0.0
                elif mouse_dev is not None:
                    now2 = time.monotonic()
                    # 1.5 秒内没有首包，或 8 秒没有新包：暂时让出，去试其它鼠标。
                    if ((mouse_probe_deadline and now2 > mouse_probe_deadline)
                            or now2 - mouse_last > 8.0):
                        try:
                            mouse_dev.close()
                        except Exception:
                            pass
                        mouse_dev = None
                        rapoo_cooldown = now2 + 30.0
                        mouse_poll_next = 0.0

            time.sleep(0.05)

    # 让诊断模式可以同步取一次值
    def read_once(self):
        return self._read_keyboard()


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", ctypes.c_uint),
    ]


def _window_hwnd(root) -> int:
    """Tk 的 winfo_id() 是子窗口，DWM 属性要设置到真正的顶层窗口。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        hwnd = root.winfo_id()
        parent = user32.GetParent(ctypes.c_void_p(hwnd))
        return int(parent or hwnd)
    except Exception:
        return int(root.winfo_id())


def apply_window_style(root) -> None:
    """Win11 圆角 + 浅色标题栏；老系统不支持时静默跳过。"""
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = ctypes.c_void_p(_window_hwnd(root))

        dark = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（旧版本用 19）
            if dwm.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break

        rounded = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(rounded), ctypes.sizeof(rounded))

        # Win11 22H2+：标题栏/边框/文字尽量跟浅色内容一致
        def colorref(value: str) -> int:
            value = value.lstrip("#")
            r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
            return r | (g << 8) | (b << 16)
        for attr, color in ((35, BG), (34, PANEL_LINE), (36, TEXT)):
            c = ctypes.c_uint(colorref(color))
            dwm.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(c), ctypes.sizeof(c))
    except Exception:
        pass


def center_on_active_monitor(root, width: int, height: int) -> None:
    """居中到鼠标所在显示器，而不是固定 128,128。"""
    try:
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, ctypes.c_uint]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(_MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        if user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
            work = info.rcWork
            x = work.left + max(0, (work.right - work.left - width) // 2)
            y = work.top + max(0, (work.bottom - work.top - height) // 2)
            root.geometry(f"{width}x{height}+{x}+{y}")
            return
    except Exception:
        pass

    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.monitor = Monitor()
        self.rows = {}
        self.pin = False
        self._last_tick = 0.0
        self._tip_job = None
        self._tip_key = None
        self._hover_key = None
        self.tip = None
        self.W = 470
        self.H = 312
        self.TILE_Y = 60
        self.TILE_H = 216
        self.pct_font = None
        self.unit_font = None
        self.subtitle_txt = None
        self._fb_win = None
        self._fb_text = None
        self._fb_status = None
        self._fb_report = None
        self._fb_raw = None

        root.withdraw()
        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda e: self.close())
        root.bind("<F5>", lambda e: self.monitor.refresh_now())
        root.bind("<Control-r>", lambda e: self.monitor.refresh_now())
        root.bind("<FocusOut>", lambda e: self._hide_tip())

        # Windows 125%/150% 缩放下，Tk 的 geometry 仍是物理像素。
        # 先按 96 DPI 的逻辑尺寸画，再整体放大，字体由 Tk 自己按 DPI 放大。
        # 系统 DPI 可能是 96，而当前显示器是 120（每显示器缩放），所以必须取窗口 DPI。
        root.update_idletasks()
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(root.winfo_id()))
        except Exception:
            dpi = 0
        if dpi <= 0:
            try:
                dpi = int(ctypes.windll.user32.GetDpiForSystem())
            except Exception:
                dpi = 96
        if dpi <= 0:
            dpi = 96
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        self.S = dpi / 96.0
        if self.S < 1.0:
            self.S = 1.0
        win_w = int(round(self.W * self.S))
        win_h = int(round(self.H * self.S))
        families = set(tkfont.families(root))
        num_family = (FONT_NUM_LIGHT if FONT_NUM_LIGHT in families
                      else "Segoe UI Light" if "Segoe UI Light" in families
                      else FONT_NUM if FONT_NUM in families else "Segoe UI")
        text_family = (FONT_TEXT if FONT_TEXT in families
                       else "Segoe UI" if "Segoe UI" in families else FONT)
        icon_family = ("Segoe Fluent Icons" if "Segoe Fluent Icons" in families
                       else "Segoe MDL2 Assets" if "Segoe MDL2 Assets" in families
                       else "Segoe UI Symbol")
        self.num_family = num_family
        self.text_family = text_family
        self.icon_font = (icon_family, 13)
        self.pct_font = tkfont.Font(root=root, family=num_family,
                                    size=22, weight="normal")
        self.unit_font = tkfont.Font(root=root, family=text_family,
                                     size=10, weight="normal")
        root.resizable(False, False)
        center_on_active_monitor(root, win_w, win_h)

        self.canvas = tk.Canvas(root, width=win_w, height=win_h,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._build_header()
        self._build_row("kb", 22, "keyboard")
        self._build_row("mouse", 242, "mouse")
        self._build_footer()
        self._build_tooltip()
        # 所有静态坐标按 DPI 放大；字体已经由 Tk 按 DPI 放大，不需要再乘。
        if abs(self.S - 1.0) > 0.001:
            self.canvas.scale("all", 0, 0, self.S, self.S)

        self.root.deiconify()
        self.root.update_idletasks()
        apply_window_style(self.root)
        self.root.after(60, lambda: apply_window_style(self.root))
        self.monitor.start()
        self.root.after(200, self._tick)

    # ---------- 绘制 ----------
    def _build_header(self):
        c = self.canvas
        # 窗口背景：顶部略亮的纵向渐变（先画，垫在所有元素下面）
        for _i in range(26):
            _t = _i / 25.0
            _col = _mix_hex("#0E1523", BG, _t)
            _y0 = self.H * _i / 26.0
            _y1 = self.H * (_i + 1) / 26.0 + 1
            c.create_rectangle(0, _y0, self.W, _y1, fill=_col, outline="")
        # 左上角小电池标
        round_rect(c, 22, 18, 48, 44, 8, fill="#0E2B24", outline="#1F4A3E", width=1)
        round_rect(c, 29, 28, 40, 36, 2, fill=BG, outline=GREEN, width=1)
        c.create_rectangle(40, 30, 42, 34, fill=GREEN, outline="")
        c.create_polygon(34, 29, 31, 33, 34, 33, 32, 38, 38, 32, 35, 32,
                         fill=GREEN, outline="")

        c.create_text(58, 14, text="2.4G 电量", anchor="nw",
                      fill=TEXT, font=(FONT, 17, "bold"))
        self.subtitle_txt = c.create_text(59, 40, text="正在检测", anchor="nw",
                                          fill=MUTED, font=(FONT, 8))

        # 刷新：图标 + 悬停底色
        self.btn_refresh_bg = round_rect(c, 388, 18, 414, 44, 8,
                                         fill=BG, outline="")
        refresh = c.create_text(401, 31, text="\uE72C", anchor="center",
                                fill=MUTED, font=self.icon_font)
        for item in (self.btn_refresh_bg, refresh):
            c.addtag_withtag("btn_refresh", item)
        c.tag_bind("btn_refresh", "<Button-1>",
                   lambda e: self.monitor.refresh_now())
        c.tag_bind("btn_refresh", "<Enter>", lambda e: self._hover_refresh(True))
        c.tag_bind("btn_refresh", "<Leave>", lambda e: self._hover_refresh(False))
        self.refresh_icon = refresh

        # 置顶：图钉
        self.btn_pin_bg = round_rect(c, 420, 18, 446, 44, 8,
                                     fill=BG, outline="")
        pin = c.create_text(433, 31, text="\uE718", anchor="center",
                            fill=MUTED, font=self.icon_font)
        for item in (self.btn_pin_bg, pin):
            c.addtag_withtag("btn_pin", item)
        c.tag_bind("btn_pin", "<Button-1>", lambda e: self._toggle_pin())
        c.tag_bind("btn_pin", "<Enter>", lambda e: self._hover_pin(True))
        c.tag_bind("btn_pin", "<Leave>", lambda e: self._hover_pin(False))
        self.pin_icon = pin

        # 一块整体面板，两行设备放在里面
        round_rect(c, 16, 75, 458, 259, 16, fill=SHADOW, outline="")
        round_rect(c, 14, 72, 456, 256, 16,
                   fill=PANEL, outline=PANEL_LINE, width=1)

    def _hover_refresh(self, on: bool):
        c = self.canvas
        c.itemconfigure(self.btn_refresh_bg, fill=BTN_HOVER if on else BG)
        c.itemconfigure(self.refresh_icon, fill=TEXT if on else MUTED)

    def _hover_pin(self, on: bool):
        c = self.canvas
        if self.pin:
            c.itemconfigure(self.btn_pin_bg, fill="#14355C")
        else:
            c.itemconfigure(self.btn_pin_bg, fill=BTN_HOVER if on else BG)

    def _build_row(self, key, tx, kind):
        c = self.canvas
        tag = f"row_{key}"
        items = []
        ty = self.TILE_Y
        cx = tx + 103
        accent = ACCENT if kind == "keyboard" else ACCENT_MOUSE
        ring_c = ty + 106
        ring_r = 30

        # 方块卡片 + 顶部主题色高光线
        bg = round_rect(c, tx, ty, tx + 206, ty + 216, 14,
                        fill=PANEL, outline="")
        items.append(bg)
        items.append(round_rect(c, tx + 18, ty + 5, tx + 188, ty + 8, 2,
                                fill=_mix_hex(accent, PANEL, 0.4), outline=""))

        # 设备图标：主题色光晕 + 图标
        glow = _mix_hex(accent, PANEL, 0.6)
        items.append(c.create_oval(cx - 26, ty + 12, cx + 26, ty + 64,
                                   fill=glow, outline=""))
        cy = ty + 38
        if kind == "keyboard":
            items.append(round_rect(c, cx - 21, cy - 13, cx + 21, cy + 13, 6,
                                    fill="", outline=accent, width=1))
            for ry in (-5, 0, 5):
                items.append(c.create_line(cx - 15, cy + ry, cx + 15, cy + ry,
                                            fill=accent, capstyle="round"))
            items.append(c.create_line(cx - 9, cy + 10, cx + 9, cy + 10,
                                        fill=accent, capstyle="round"))
            name = "自动识别键盘"
        else:
            items.append(round_rect(c, cx - 11, cy - 15, cx + 11, cy + 15, 8,
                                    fill="", outline=accent, width=1))
            items.append(c.create_line(cx, cy - 10, cx, cy - 2,
                                        fill=accent, capstyle="round"))
            name = "自动识别鼠标"

        # 环形电量：轨道 + 填充弧 + 顶端光点
        bb = (cx - ring_r, ring_c - ring_r, cx + ring_r, ring_c + ring_r)
        track = c.create_arc(*bb, start=0, extent=359.9, style="arc",
                             width=7, outline=TRACK)
        arc = c.create_arc(*bb, start=90, extent=0, style="arc",
                           width=7, outline=DIM, state="hidden")
        tip = c.create_oval(cx - 5, ring_c - ring_r - 5, cx + 5,
                            ring_c - ring_r + 5, fill=DIM, outline="",
                            state="hidden")

        name_txt = c.create_text(cx, ty + 152, text=name, anchor="center",
                                 fill=MUTED, font=(FONT, 9))
        pct_txt = c.create_text(cx, ring_c, text="--", anchor="center",
                                fill=DIM, font=self.pct_font)
        pct_unit = c.create_text(cx, ring_c, text="", anchor="nw",
                                 fill=DIM, font=self.unit_font)

        sdot = c.create_oval(cx - 42, ty + 180, cx - 36, ty + 186,
                             fill=DIM, outline="")
        stxt = c.create_text(cx, ty + 183, text="正在检测", anchor="center",
                             fill=MUTED, font=(FONT, 8))

        # 胶囊默认隐藏，只在充电/低电/未连接/未适配时出现
        pill = round_rect(c, cx - 24, ty + 197, cx + 24, ty + 211, 8,
                          fill="#1B2536", outline="")
        pill_txt = c.create_text(cx, ty + 204, text="", anchor="center",
                                 fill=MUTED, font=(FONT, 8))
        c.itemconfigure(pill, state="hidden")
        c.itemconfigure(pill_txt, state="hidden")

        items += [name_txt, pct_txt, pct_unit, track, arc, tip,
                  sdot, stxt, pill, pill_txt]
        for item in items:
            c.addtag_withtag(tag, item)
        c.tag_bind(tag, "<Enter>", lambda e, k=key: self._row_enter(k))
        c.tag_bind(tag, "<Leave>", lambda e, k=key: self._row_leave(k))

        self.rows[key] = {
            "bg": bg, "name_txt": name_txt, "tx": tx, "ty": ty, "cx": cx,
            "ring_c": ring_c, "ring_r": ring_r,
            "pct_txt": pct_txt, "pct_unit": pct_unit,
            "arc": arc, "tip": tip,
            "pill": pill, "pill_txt": pill_txt,
            "sdot": sdot, "stxt": stxt, "tip_text": "",
        }
    def _build_footer(self):
        c = self.canvas
        self.updated_txt = c.create_text(22, 288, text="正在读取设备…",
                                         anchor="nw", fill=MUTED, font=(FONT, 8))
        self.fb_btn = c.create_text(398, 288, text="提交设备信息", anchor="ne",
                                    fill=BLUE, font=(FONT, 8))
        c.tag_bind(self.fb_btn, "<Button-1>", lambda e: self._open_feedback())
        c.tag_bind(self.fb_btn, "<Enter>",
                   lambda e: c.itemconfigure(self.fb_btn, fill=AMBER))
        c.tag_bind(self.fb_btn, "<Leave>",
                   lambda e: c.itemconfigure(self.fb_btn, fill=BLUE))
        self.update_txt = c.create_text(448, 288, text="检查更新", anchor="ne",
                                        fill=DIM, font=(FONT, 8))
        c.tag_bind(self.update_txt, "<Button-1>", lambda e: self._check_update_click())
        c.tag_bind(self.update_txt, "<Enter>",
                   lambda e: c.itemconfigure(self.update_txt, fill=BLUE))
        c.tag_bind(self.update_txt, "<Leave>",
                   lambda e: c.itemconfigure(self.update_txt, fill=DIM))

    def _build_tooltip(self):
        self.tip = tk.Toplevel(self.root)
        self.tip.title("")  # 不要和主窗口同名，否则单实例查找会找错窗口
        self.tip.withdraw()
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.configure(bg=CARD_LINE)
        self.tip_label = tk.Label(self.tip, text="", bg=PANEL, fg=TEXT,
                                  font=(FONT, 8), justify="left",
                                  padx=9, pady=7, wraplength=330)
        self.tip_label.pack(padx=1, pady=1)

    def _row_enter(self, key):
        self._cancel_tip()
        self._hover_key = key
        if key in self.rows:
            self.canvas.itemconfigure(self.rows[key]["bg"], fill=HOVER)
        self._tip_job = self.root.after(450, lambda: self._show_tip(key))

    def _row_leave(self, key):
        if self._hover_key == key:
            self._hover_key = None
        if key in self.rows:
            self.canvas.itemconfigure(self.rows[key]["bg"], fill=PANEL)
        self._cancel_tip()
        self._hide_tip()

    def _cancel_tip(self):
        if self._tip_job is not None:
            try:
                self.root.after_cancel(self._tip_job)
            except Exception:
                pass
            self._tip_job = None

    def _show_tip(self, key):
        self._tip_job = None
        row = self.rows.get(key)
        if not row or not row.get("tip_text") or self.tip is None:
            return
        self._tip_key = key
        self.tip_label.configure(text=row["tip_text"])
        self.tip.update_idletasks()
        x = self.root.winfo_pointerx() + 14
        y = self.root.winfo_pointery() + 16
        tw = self.tip.winfo_reqwidth()
        th = self.tip.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if x + tw > sw:
            x = self.root.winfo_pointerx() - tw - 14
        if y + th > sh:
            y = self.root.winfo_pointery() - th - 16
        self.tip.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.tip.deiconify()

    def _hide_tip(self):
        self._tip_key = None
        if self.tip is not None:
            try:
                self.tip.withdraw()
            except Exception:
                pass

    # ---------- 设备反馈 ----------    # ---------- 设备反馈 ----------
    def _open_feedback(self):
        if self._fb_win is not None:
            try:
                if self._fb_win.winfo_exists():
                    self._fb_win.deiconify()
                    self._fb_win.lift()
                    return
            except Exception:
                pass
            self._fb_win = None

        win = tk.Toplevel(self.root)
        self._fb_win = win
        win.title("提交设备信息")
        win.configure(bg=PANEL)
        win.resizable(True, True)
        try:
            win.attributes("-topmost", False)
        except Exception:
            pass
        try:
            x = self.root.winfo_rootx() + 40
            y = self.root.winfo_rooty() + 40
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

        tk.Label(win, text="提交设备信息", bg=PANEL, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(
            win, bg=PANEL, fg=MUTED, font=(FONT, 8), justify="left", anchor="w",
            text="只收集 HID 设备描述符和报告描述符，不含按键内容、文件名、"
                 "剪贴板或个人文件。点击“发送”才联网，平时仍然零网络。",
        ).pack(fill="x", padx=14, pady=(0, 8))

        self._fb_text = tk.Text(win, width=74, height=18, wrap="none",
                                bg="#0B1120", fg=TEXT, font=("Consolas", 8),
                                padx=10, pady=8, relief="solid", bd=1,
                                highlightthickness=0)
        self._fb_text.pack(fill="both", expand=True, padx=14)
        self._fb_text.insert("1.0", "正在收集设备信息…")
        self._fb_text.configure(state="disabled")

        self._fb_status = tk.Label(win, text="", bg=PANEL, fg=MUTED,
                                   font=(FONT, 8), anchor="w", justify="left")
        self._fb_status.pack(fill="x", padx=14, pady=(6, 2))

        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill="x", padx=14, pady=(0, 12))
        send = tk.Button(bar, text="发送给开发者", command=self._fb_send,
                         bg="#122A42", fg=BLUE, activebackground="#1B3A5C",
                         activeforeground=BLUE, relief="flat", font=(FONT, 9),
                         padx=12, pady=5)
        copy = tk.Button(bar, text="复制报告", command=self._fb_copy,
                         bg="#1B2740", fg=TEXT, activebackground="#24324A",
                         activeforeground=TEXT, relief="flat", font=(FONT, 9),
                         padx=12, pady=5)
        save = tk.Button(bar, text="保存为文件", command=self._fb_save,
                         bg="#1B2740", fg=TEXT, activebackground="#24324A",
                         activeforeground=TEXT, relief="flat", font=(FONT, 9),
                         padx=12, pady=5)
        close = tk.Button(bar, text="关闭", command=self._fb_close,
                          bg="#1B2740", fg=TEXT, activebackground="#24324A",
                          activeforeground=TEXT, relief="flat", font=(FONT, 9),
                          padx=12, pady=5)
        send.pack(side="left")
        copy.pack(side="left", padx=(8, 0))
        save.pack(side="left", padx=(8, 0))
        close.pack(side="right")

        win.protocol("WM_DELETE_WINDOW", self._fb_close)

        self._fb_report = None
        self._fb_raw = None
        threading.Thread(target=self._fb_collect, daemon=True).start()

    def _fb_close(self):
        if self._fb_win is not None:
            try:
                self._fb_win.destroy()
            except Exception:
                pass
        self._fb_win = None
        self._fb_text = None
        self._fb_status = None

    def _fb_collect(self):
        try:
            text, raw = build_device_report()
        except Exception as e:
            text, raw = f"收集失败: {e!r}", b""
        self._fb_report = text
        self._fb_raw = raw
        self.root.after(0, self._fb_populate)

    def _fb_populate(self):
        if self._fb_text is None:
            return
        try:
            self._fb_text.configure(state="normal")
            self._fb_text.delete("1.0", "end")
            self._fb_text.insert("1.0", self._fb_report or "")
            self._fb_text.configure(state="disabled")
        except Exception:
            pass
        hint = "" if FEEDBACK_ENDPOINT else "（未配置接收端点，将回退为复制 + 打开提交页）"
        self._fb_set_status("报告已生成。" + hint)

    def _fb_set_status(self, msg):
        if self._fb_status is not None:
            try:
                self._fb_status.configure(text=msg)
            except Exception:
                pass

    def _fb_copy(self):
        if not self._fb_report:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._fb_report)
            self._fb_set_status("报告已复制到剪贴板。")
        except Exception as e:
            self._fb_set_status(f"复制失败: {e!r}")

    def _fb_save(self):
        if not self._fb_report:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(_desktop_dir(), f"设备反馈-{stamp}.txt")
        rawpath = os.path.join(_desktop_dir(), f"设备反馈-{stamp}-描述符.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._fb_report)
            if self._fb_raw:
                with open(rawpath, "wb") as f:
                    f.write(self._fb_raw)
            self._fb_set_status(f"已保存到桌面：{os.path.basename(path)}")
            try:
                os.startfile(_desktop_dir())
            except Exception:
                pass
        except Exception as e:
            self._fb_set_status(f"保存失败: {e!r}")

    def _fb_send(self):
        if not self._fb_report:
            return
        self._fb_set_status("正在发送…")
        if FEEDBACK_ENDPOINT:
            def run():
                ok, msg = upload_report(self._fb_report, self._fb_raw or b"")
                self.root.after(0, lambda: self._fb_set_status(
                    ("已发送：" + msg) if ok else msg))
            threading.Thread(target=run, daemon=True).start()
        else:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(self._fb_report)
            except Exception:
                pass
            self._fb_set_status("报告已复制。将在浏览器打开提交页，请 Ctrl+V 粘贴后提交。")
            try:
                os.startfile(_issue_url())
            except Exception as e:
                self._fb_set_status(f"打开浏览器失败: {e!r}")


    # ---------- 检查更新 ----------
    def _check_update_click(self):
        if getattr(self, "_checking", False):
            return
        self._checking = True
        self.canvas.itemconfigure(self.update_txt, text="检查中…")

        def run():
            try:
                info = check_update()
            except Exception as e:
                self.root.after(0, lambda: self._update_prompt(None, str(e)))
                return
            self.root.after(0, lambda: self._update_prompt(info, None))
        threading.Thread(target=run, daemon=True).start()

    def _update_prompt(self, info, err):
        self._checking = False
        self.canvas.itemconfigure(self.update_txt, text="检查更新")
        if err:
            messagebox.showwarning("检查更新", f"检查更新失败：{err}")
            return
        latest = str((info or {}).get("version") or "").strip()
        if not latest:
            messagebox.showwarning("检查更新", "无法获取版本信息。")
            return
        if _version_tuple(latest) <= _version_tuple(APP_VERSION):
            messagebox.showinfo("检查更新", f"已是最新版本 v{APP_VERSION}")
            return
        dl = (info or {}).get("download_url") or ""
        notes = str((info or {}).get("notes") or "").strip()
        msg = f"发现新版本 v{latest}（当前 v{APP_VERSION}）"
        if notes:
            msg += "\n\n更新内容：\n" + notes
        msg += "\n\n是否下载并更新？"
        if not messagebox.askyesno("发现新版本", msg):
            return
        self._download_update(latest, dl)

    def _download_update(self, latest, url):
        if not url:
            messagebox.showerror("更新", "没有可用的下载地址。")
            return
        exe = _exe_path()
        if not exe:
            messagebox.showinfo("更新", f"检测到新版本 v{latest}。\n当前为源码运行，请手动下载更新。")
            return
        win = tk.Toplevel(self.root)
        win.title("更新")
        win.configure(bg=PANEL)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        try:
            win.grab_set()
        except Exception:
            pass
        lbl = tk.Label(win, text=f"正在下载 v{latest}…", bg=PANEL, fg=TEXT,
                       font=(FONT, 10), padx=28, pady=16)
        lbl.pack()
        win.update_idletasks()

        new_path = os.path.join(os.path.dirname(exe), "_battery_update.tmp")

        def progress(done, total):
            if total:
                pct = min(99, int(done * 100 / total))
                lbl.config(text=f"正在下载 v{latest}… {pct}%")
            else:
                lbl.config(text=f"正在下载 v{latest}… {done // 1024} KB")
            win.update_idletasks()

        def run():
            try:
                download_file(url, new_path, progress)
                self.root.after(0, lambda: self._finish_update(win, exe, new_path, latest))
            except Exception as e:
                self.root.after(0, lambda: self._update_download_fail(win, str(e)))
        threading.Thread(target=run, daemon=True).start()

    def _finish_update(self, win, exe, new_path, latest):
        try:
            win.destroy()
        except Exception:
            pass
        try:
            _apply_update(exe, new_path)
        except Exception as e:
            messagebox.showerror("更新", f"准备替换失败：{e!r}")
            return
        messagebox.showinfo("更新", f"v{latest} 下载完成，程序即将自动重启。")
        self.root.destroy()

    def _update_download_fail(self, win, msg):
        try:
            win.destroy()
        except Exception:
            pass
        messagebox.showerror("更新", f"下载失败：{msg}")


    def _toggle_pin(self):
        self.pin = not self.pin
        self.root.attributes("-topmost", self.pin)
        c = self.canvas
        c.itemconfigure(self.btn_pin_bg, fill="#14355C" if self.pin else BG)
        c.itemconfigure(self.pin_icon, fill=BLUE if self.pin else MUTED)

    # ---------- 刷新 ----------
    def _update_card(self, key, state: DeviceState):
        c = self.canvas
        row = self.rows[key]
        tx = row["tx"]; ty = row["ty"]; cx = row["cx"]
        s = self.S

        if state.name:
            c.itemconfigure(row["name_txt"], text=_elide(state.name, 14))

        bar_color = BLUE if state.charging else (
            level_color(state.battery) if state.online else DIM)
        if not state.online:
            pct_color = DIM
        elif state.charging:
            pct_color = BLUE
        elif state.battery is not None and state.battery <= 20:
            pct_color = RED
        else:
            pct_color = TEXT
        pct_text = "--" if state.battery is None else str(state.battery)
        c.itemconfigure(row["pct_txt"], text=pct_text, fill=pct_color)

        ring_c = row["ring_c"]; ring_r = row["ring_r"]

        # 百分比：数字 + % 作为整体居中在圆环里
        if state.battery is None:
            c.itemconfigure(row["pct_unit"], state="hidden")
            c.coords(row["pct_txt"], cx * s, ring_c * s)
        else:
            num_w = self.pct_font.measure(pct_text) / s
            unit_w = self.unit_font.measure("%") / s
            gap = 3
            left = cx - (num_w + gap + unit_w) / 2
            c.itemconfigure(row["pct_unit"], text="%", state="normal",
                            fill=pct_color)
            c.coords(row["pct_txt"], (left + num_w / 2) * s, ring_c * s)
            c.coords(row["pct_unit"], (left + num_w + gap) * s,
                     (ring_c - 6) * s)

        # 环形电量：填充弧 + 顶端光点
        if state.online and state.battery is not None:
            pct = max(0, min(100, state.battery))
            extent = -359.9 * pct / 100.0
            c.itemconfigure(row["arc"], state="normal", outline=bar_color,
                            extent=extent)
            if pct > 0:
                ang = math.radians(90.0 + extent)
                dx = cx + ring_r * math.cos(ang)
                dy = ring_c - ring_r * math.sin(ang)
                c.coords(row["tip"], (dx - 4) * s, (dy - 4) * s,
                         (dx + 4) * s, (dy + 4) * s)
                c.itemconfigure(row["tip"], state="normal", fill=bar_color)
            else:
                c.itemconfigure(row["tip"], state="hidden")
        else:
            c.itemconfigure(row["arc"], state="hidden")
            c.itemconfigure(row["tip"], state="hidden")

        # 状态：正常只留圆点 + 文字；胶囊只给特殊状态
        status_text = state.status
        show_pill = False
        pill_text = ""
        pill_fill = "#1B2536"
        pill_fg = MUTED
        dot_color = DIM
        if state.online:
            if state.charging:
                show_pill = True
                pill_text, pill_fill, pill_fg = "充电中", "#122A42", BLUE
                dot_color = BLUE
                if status_text.strip() == "充电中":
                    status_text = "2.4G 已连接"
            elif state.battery is not None and state.battery <= 20:
                show_pill = True
                pill_text, pill_fill, pill_fg = "电量低", "#2B2112", AMBER
                dot_color = AMBER
            else:
                dot_color = GREEN
        else:
            if "暂不支持" in status_text:
                show_pill = True
                pill_text, pill_fill, pill_fg = "未适配", "#2B2112", AMBER
                dot_color = AMBER
            else:
                show_pill = True
                pill_text, pill_fill, pill_fg = "未连接", "#1B2536", MUTED
                dot_color = DIM

        c.itemconfigure(row["sdot"], fill=dot_color)
        c.itemconfigure(row["stxt"], text=_elide(status_text, 20),
                        fill=BLUE if state.charging else MUTED)
        if show_pill:
            c.itemconfigure(row["pill"], state="normal", fill=pill_fill)
            c.itemconfigure(row["pill_txt"], state="normal",
                            text=pill_text, fill=pill_fg)
        else:
            c.itemconfigure(row["pill"], state="hidden")
            c.itemconfigure(row["pill_txt"], state="hidden")

        # 技术细节只放在悬停提示里
        parts = []
        if state.name:
            parts.append(state.name)
        if state.detail:
            parts.append(state.detail)
        if state.updated:
            parts.append(time.strftime("%H:%M:%S 读取",
                                       time.localtime(state.updated)))
        row["tip_text"] = "\n".join(parts)
        if (self._tip_key == key and self.tip is not None
                and self.tip.winfo_ismapped()):
            self.tip_label.configure(text=row["tip_text"])

    def _tick(self):
        try:
            kb, mouse = self.monitor.snapshot()
            self._update_card("kb", kb)
            self._update_card("mouse", mouse)
            online = int(bool(kb.online)) + int(bool(mouse.online))
            if online:
                subtitle = f"{online} 台设备在线"
            elif kb.updated or mouse.updated:
                subtitle = "设备未响应"
            else:
                subtitle = "正在检测"
            self.canvas.itemconfigure(self.subtitle_txt, text=subtitle)
            latest = max([t for t in (kb.updated, mouse.updated) if t] or [0])
            if latest:
                self.canvas.itemconfigure(
                    self.updated_txt,
                    text=f"{time.strftime('%H:%M', time.localtime(latest))} 更新"
                         f"  ·  {REFRESH_SECONDS}s 刷新")
            else:
                self.canvas.itemconfigure(self.updated_txt,
                                          text="正在读取设备…")
        except Exception:
            pass
        self.root.after(300, self._tick)

    def close(self):
        self._cancel_tip()
        try:
            if self.tip is not None:
                self.tip.destroy()
        except Exception:
            pass
        try:
            self.monitor.stop()
        finally:
            self.root.destroy()


def acquire_single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, APP_TITLE)
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return None
    return handle




def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _desktop_dir() -> str:
    """返回当前用户桌面路径，拿不到就退回用户目录。"""
    try:
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if ctypes.windll.shell32.SHGetFolderPathW(0, 0x0010, 0, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.expanduser("~")


def build_device_report() -> tuple[str, bytes]:
    """生成适配诊断报告（纯 HID 元数据，不含任何个人内容）。

    返回 (可读文本, 报告描述符原始字节拼块)。原始描述符是私有协议适配的
    关键输入，单独拼成一段方便日后直接喂给解析脚本。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        ver = sys.getwindowsversion()
        osline = f"Windows {ver.major}.{ver.minor}.{ver.build}"
    except Exception:
        osline = "Windows ?"
    lines = [
        "2.4G 电量查看器 · 设备适配反馈",
        f"app_version={APP_VERSION}",
        f"time={now}",
        f"os={osline}",
        "",
        "隐私说明：本报告只含 HID 设备描述符（VID/PID/usage/报告长度/设备",
        "名称）和能力表（报告描述符如系统提供），不含键盘按键内容、文件名、",
        "剪贴板、网络信息或个人文件。",
        "",
    ]
    raw_parts = [f"# app={APP_VERSION}\n# time={now}\n".encode("utf-8")]
    try:
        devices = enumerate_hid()
    except Exception as e:
        devices = []
        lines.append(f"枚举失败: {e!r}")
    groups: dict = {}
    for d in devices:
        if getattr(d, "error", ""):
            continue
        key = getattr(d, "group", "") or d.path
        groups.setdefault(key, []).append(d)
    for gi, ifaces in enumerate(groups.values(), 1):
        first = ifaces[0]
        vendor = vendor_name(first.vid) or "未知厂商"
        kinds = _group_kinds(ifaces)
        kindline = ""
        if "keyboard" in kinds:
            kindline += "键盘"
        if "mouse" in kinds:
            kindline += "/鼠标" if kindline else "鼠标"
        if not kindline:
            kindline = "其它 HID"
        lines.append(f"[设备 {gi}] {vendor} {first.vid:04X}:{first.pid:04X}"
                     f"  ({kindline})")
        raw_parts.append(
            f"# dev {gi}: {vendor} {first.vid:04X}:{first.pid:04X}\n".encode("utf-8"))
        for d in ifaces:
            lines.append(f"  usage={d.usage_page:04X}/{d.usage:04X}"
                         f"  input={d.input_len} output={d.output_len}"
                         f"  feature={d.feature_len}")
            if d.product:
                lines.append(f"    product={d.product!r}")
            if d.manufacturer:
                lines.append(f"    manufacturer={d.manufacturer!r}")
            desc, _err = report_descriptor(d.path)
            if desc:
                lines.append(f"    report_descriptor={len(desc)} bytes")
                raw_parts.append((
                    f"# iface {d.usage_page:04X}/{d.usage:04X}"
                    f" in={d.input_len} out={d.output_len} feat={d.feature_len}\n"
                ).encode("utf-8"))
                raw_parts.append(desc + b"\n")
            try:
                with HidDevice(d.path, write=False) as dev:
                    for rt, name in ((HidP_Feature, "Feature"),
                                     (HidP_Input, "Input")):
                        blocks = caps_blocks(dev, rt)
                        if blocks:
                            lines.append(f"    {name} caps:")
                            for b in blocks:
                                lines.append(
                                    f"      usage={b['usage']} report={b['report']}"
                                    f" bits={b['bit_size']}x{b['report_count']}"
                                    f" range={b['logical_min']}..{b['logical_max']}")
            except Exception as e:
                lines.append(f"    caps 读取失败: {e!r}")
        lines.append("")
    text = "\n".join(lines)
    raw = b"\n===\n".join(raw_parts)
    return text, raw


def _issue_url() -> str:
    """没配接收端点时，打开 GitHub Issue 预填页（正文让用户粘贴已复制的报告）。"""
    body = ("请粘贴本软件里“复制报告”得到的设备反馈内容（Ctrl+V）：\n\n"
            "（在此粘贴报告）")
    qs = urllib.parse.urlencode({"title": "设备适配请求", "body": body})
    return f"https://github.com/{FEEDBACK_REPO}/issues/new?{qs}"


def upload_report(text: str, raw: bytes) -> tuple[bool, str]:
    """把报告 POST 到 FEEDBACK_ENDPOINT，返回 (是否成功, 说明或链接)。"""
    if not FEEDBACK_ENDPOINT:
        return False, "未配置接收端点"
    payload = {
        "app": "2.4G-battery-viewer",
        "version": APP_VERSION,
        "text": text,
        "descriptor_hex": raw.hex(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        FEEDBACK_ENDPOINT, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": FEEDBACK_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=FEEDBACK_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                j = json.loads(body)
                if isinstance(j, dict) and j.get("url"):
                    return True, j["url"]
            except Exception:
                pass
            return True, f"已发送（HTTP {resp.status}）"
    except Exception as e:
        return False, f"发送失败: {e!r}"


def _version_tuple(v: str) -> tuple[int, ...]:
    """把 'v1.3.2' / '1.3.2' / '1.3.2-beta' 解析成可比较的 (1,3,2)。"""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for seg in v.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_update() -> dict:
    """读取仓库里的 version.json，返回 {'version','download_url','notes'}；失败抛异常。"""
    url = f"https://api.github.com/repos/{UPDATE_REPO}/contents/version.json"
    req = urllib.request.Request(
        url, headers={"User-Agent": UPDATE_UA,
                      "Accept": "application/vnd.github+json",
                      "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        meta = json.loads(resp.read().decode("utf-8", "replace"))
    raw = base64.b64decode(meta.get("content", "")).decode("utf-8", "replace")
    return json.loads(raw)


def download_file(url: str, dest: str, progress_cb=None) -> int:
    """下载到 dest，返回字节数；progress_cb(done, total) 周期回调。"""
    req = urllib.request.Request(url, headers={"User-Agent": UPDATE_UA})
    with urllib.request.urlopen(req, timeout=UPDATE_DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    return done


def _exe_path() -> str | None:
    """冻结成 exe 时返回 exe 自身路径，否则返回 None（源码运行不替换自己）。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def _apply_update(exe_path: str, new_path: str) -> None:
    """写一个批处理：等程序退出后替换 exe、重启、自我删除。"""
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        ":wait\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        'del /f /q "%~1" >nul 2>&1\r\n'
        'if exist "%~1" goto wait\r\n'
        'move /y "%~2" "%~1" >nul\r\n'
        'start "" "%~1"\r\n'
        'del "%~f0" >nul 2>&1\r\n'
    )
    bat = os.path.join(os.path.dirname(exe_path), "_update.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(script)
    subprocess.Popen(
        ["cmd", "/c", bat, exe_path, new_path],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def scan_hid() -> str:
    """兼容旧的 --scan：把报告写成两个文件并打印。"""
    text, raw = build_device_report()
    path = os.path.join(_desktop_dir(), "设备扫描.txt")
    rawpath = os.path.join(_desktop_dir(), "设备扫描-报告描述符.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        path = ""
    try:
        with open(rawpath, "wb") as f:
            f.write(raw)
    except Exception:
        rawpath = ""
    try:
        print(text)
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        for p in (path, rawpath):
            if p:
                try:
                    os.startfile(p)
                except Exception:
                    pass
    return path


def diagnose():
    mon = Monitor()
    kb = mon.read_once()
    print("keyboard:", kb)
    target = mon._find_mouse()
    print("mouse interface:", target)
    if target:
        try:
            with HidDevice(target.path) as dev:
                dev.drain()
                for _ in range(8):
                    data = dev.read_input(timeout_ms=1000)
                    if data:
                        print("mouse packet:", data.hex(" "))
                        if len(data) >= 7:
                            print("  battery:", data[6], "status:", data[5])
                        break
        except Exception as e:
            print("mouse error:", repr(e))


def main():
    if "--scan" in sys.argv:
        scan_hid()
        return
    if "--diag" in sys.argv:
        diagnose()
        return
    if acquire_single_instance() is None:
        return
    root = tk.Tk()
    try:
        # 让 tk 使用系统中文字体
        tkfont.nametofont("TkDefaultFont").configure(family=FONT)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
