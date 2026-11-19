# -*- coding: utf-8 -*-
"""纯函数单元测试：不依赖任何 HID 硬件，覆盖各品牌协议解析与工具函数。"""
import types

import battery_viewer as bv


# ---------- 版本 / 更新 ----------
def test_version_tuple():
    assert bv._version_tuple("v1.6.7") == (1, 6, 7)
    assert bv._version_tuple("1.6.7") == (1, 6, 7)
    assert bv._version_tuple("1.6") == (1, 6, 0)
    assert bv._version_tuple("1.3.2-beta") == (1, 3, 2)
    assert bv._version_tuple("") == (0, 0, 0)
    assert bv._version_tuple("abc") == (0, 0, 0)
    assert bv._version_tuple("v2") == (2, 0, 0)


def test_fallback_url():
    assert bv._fallback_url(
        "https://ghfast.top/https://github.com/a/b/c.exe") == "https://github.com/a/b/c.exe"
    assert bv._fallback_url(
        "https://gh-proxy.com/https://github.com/a/b") == "https://github.com/a/b"
    assert bv._fallback_url(
        "https://ghproxy.com/https://github.com/a/b") == "https://github.com/a/b"
    assert bv._fallback_url("https://github.com/a/b") is None
    assert bv._fallback_url("https://raw.githubusercontent.com/a/b") is None


def test_parse_sha256():
    h = "a" * 64
    assert bv._parse_sha256(h + "  2.4G-Battery-Viewer.exe") == h
    assert bv._parse_sha256(h.upper()) == h
    assert bv._parse_sha256("no hash here") == ""


# ---------- 显示辅助 ----------
def test_elide():
    assert bv._elide("hello", 8) == "hello"
    assert bv._elide("hello world", 8) == "hello w…"
    assert bv._elide("", 5) == ""
    assert bv._elide(None, 5) == ""


def test_level_color():
    assert bv.level_color(None) == bv.DIM
    assert bv.level_color(20) == bv.RED
    assert bv.level_color(21) == bv.AMBER
    assert bv.level_color(50) == bv.AMBER
    assert bv.level_color(51) == bv.GREEN
    assert bv.level_color(100) == bv.GREEN


def test_mix_hex():
    assert bv._mix_hex("#000000", "#FFFFFF", 0.5) == "#7f7f7f"
    assert bv._mix_hex("#000000", "#FFFFFF", 0.0) == "#000000"
    assert bv._mix_hex("#000000", "#FFFFFF", 1.0) == "#ffffff"


def test_vendor_name():
    assert bv.vendor_name(0x046D) == "Logitech"
    assert bv.vendor_name(0x3151) == "VGN"
    assert bv.vendor_name(0xFFFF) == ""


# ---------- 设备分组 / 标准 HID ----------
def _iface(page, usage):
    return types.SimpleNamespace(usage_page=page, usage=usage)


def test_group_kinds():
    assert bv._group_kinds([_iface(1, 0x06)]) == {"keyboard"}
    assert bv._group_kinds([_iface(1, 0x02)]) == {"mouse"}
    assert bv._group_kinds([_iface(1, 0x06), _iface(1, 0x02)]) == {"keyboard", "mouse"}
    assert bv._group_kinds([_iface(0xFF00, 0x02)]) == set()


def test_scale_battery():
    caps100 = types.SimpleNamespace(LogicalMin=0, LogicalMax=100)
    caps255 = types.SimpleNamespace(LogicalMin=0, LogicalMax=255)
    assert bv._scale_battery(50, caps100) == 50
    assert bv._scale_battery(255, caps255) == 100
    assert bv._scale_battery(128, caps255) == 50
    assert bv._scale_battery(None, caps100) is None
    assert bv._scale_battery(300, caps255) is None


def test_guess_kind():
    assert bv._guess_kind("Logitech G304") == "mouse"
    assert bv._guess_kind("MX Keys") == "keyboard"
    assert bv._guess_kind("G502 Lightspeed") == "mouse"
    assert bv._guess_kind("receiver") is None


# ---------- 各品牌协议解析 ----------
def test_logi_decode_battery_status():
    assert bv._logi_decode_battery(0x1000, bytes([50, 0, 0])) == (50, False)
    assert bv._logi_decode_battery(0x1000, bytes([50, 0, 1])) == (50, True)
    assert bv._logi_decode_battery(0x1000, bytes([0, 0, 0])) == (None, False)
    assert bv._logi_decode_battery(0x1000, b"") == (None, False)


def test_logi_decode_battery_voltage():
    # 4200mV -> 100%，flags 0x80 = 充电
    assert bv._logi_decode_battery(0x1001, bytes([0x10, 0x68, 0x00])) == (100, False)
    assert bv._logi_decode_battery(0x1001, bytes([0x10, 0x68, 0x80])) == (100, True)


def test_logi_decode_battery_unified():
    # discharge=0, approx=4 -> 75%
    assert bv._logi_decode_battery(0x1004, bytes([0, 4, 0])) == (75, False)
    assert bv._logi_decode_battery(0x1004, bytes([60, 0, 0])) == (60, False)


def test_ss_parse():
    assert bv._ss_parse(bytes([0x2D, 0x00, 0x00])) == (45, False)
    assert bv._ss_parse(bytes([0x2D, 0x00, 0x01])) == (45, True)
    assert bv._ss_parse(b"\x00" + bytes([0x2D, 0x00, 0x01]) + b"\x00" * 61) == (45, True)
    assert bv._ss_parse(b"") is None
    assert bv._ss_parse(bytes([0x64, 0x00, 0x01])) == (100, True)


def test_mchose_packet():
    pkt = bv._mchose_packet(0x0904)
    assert len(pkt) == 31
    assert pkt[0] == 1
    assert pkt[3] == 0x09 and pkt[4] == 0x04
    check = 0
    for b in pkt[1:7]:
        check ^= b
    assert pkt[7] == check


def test_mchose_parse():
    assert bv._mchose_parse(bytes([1, 3, 2, 0x09, 0x04, 0, 0, 1, 80])) == (80, True)
    assert bv._mchose_parse(bytes([1, 3, 2, 0x09, 0x05, 0, 0, 1, 80])) is None
    assert bv._mchose_parse(bytes([1, 3, 2, 0x09, 0x04, 0, 0, 0, 200])) is None
    assert bv._mchose_parse(b"") is None


def test_razer_report():
    rpt = bv._razer_report(0x07, 0x80, 2, 0x1F)
    assert len(rpt) == 90
    assert rpt[0] == 0x00
    assert rpt[1] == 0x1F
    assert rpt[5] == 2
    assert rpt[6] == 0x07
    assert rpt[7] == 0x80
    crc = 0
    for b in rpt[2:88]:
        crc ^= b
    assert rpt[88] == crc
    assert rpt[89] == 0x00
