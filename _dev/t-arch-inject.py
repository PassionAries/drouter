#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反向验证：把 arm64 适配的每处改动改坏，确认 t-arch.py 真的会红。

跟着 t-update-inject.py 的惯例写：每个用例「注入 → 跑判据 → 必须因为这一条
而红 → 无条件还原」。落点是**语义层**，不是被检查的那个名字。

用法（**不要**加进 scripts/test-static.sh，那里会改写源文件）：
    python _dev/t-arch-inject.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
JUDGE = os.path.join(ROOT, '_dev', 't-arch.py')

HELPER = 'backend/drouter-helper.py'
UPDATE = 'backend/drouter-update.py'
I18N = 'web/i18n.js'
OFFLINE = 'packaging/build-offline-bundle.sh'
DOCKER = 'packaging/build-docker.sh'

CASES = [
    # ---------------- A. 更新模块 ----------------
    # 把 %s 拼架构换回写死的 amd64 —— 就是 arm64 适配前的原状。
    # 判据第一条查 'offline-amd64' 是否**不在** 源码里，注入后立刻出现。
    ('A1 离线包资源名写死 amd64（arm64 会下到 x86 包）',
     UPDATE,
     "        ('offline', 'drouter-{ver}-offline-%s.tar.gz' % a,\n"
     "         '离线安装包（%s，87 MB，断网可装）' % a, True),",
     "        ('offline', 'drouter-{ver}-offline-amd64.tar.gz',\n"
     "         '离线安装包（amd64，87 MB，断网可装）', True),",
     '离线包资源名模板不含写死的 amd64'),

    # build_assets 忽略入参、一律按本机算 —— 三架构拼出同一个名字。
    # ⚠️ 不删 _deb_arch()，因为判据查的是「拼出来的结果」而非函数是否存在。
    ('A2 build_assets 忽略 arch 入参（三架构同名）',
     UPDATE,
     "    a = arch or _deb_arch()",
     "    a = _deb_arch()",
     '不同架构拼出的离线包名两两不同'),

    # deb 是 Architecture: all，跟进架构走就错了。
    ('A3 deb 包名也带上架构后缀（违背 Architecture: all）',
     UPDATE,
     "        ('deb', 'drouter_{ver}_all.deb', 'Deb 安装包（推荐，523 KB）', False),",
     "        ('deb', 'drouter_{ver}_%s.deb' % a, 'Deb 安装包（推荐，523 KB）', False),",
     'deb 本体三架构同名'),

    # arch_specific 全 False → apply 不再先用 SHA256SUMS 核对，arm64 白下 87MB。
    ('A4 离线包漏标 arch_specific（失去下载前核对）',
     UPDATE,
     "         '离线安装包（%s，87 MB，断网可装）' % a, True),",
     "         '离线安装包（%s，87 MB，断网可装）' % a, False),",
     '只有离线包标了 arch_specific'),

    # 元组少一列 → asset_list 解包 ValueError，「更新」页整块渲染不出来。
    # 这条注入会让前面几条也一起红（模块在 judge 里加载失败），属实完好：
    # 断言关键词挑的是**本条对应**的那条判据。
    ('A5 ASSETS 条目退回三元组（asset_list 解包炸）',
     UPDATE,
     "        ('docker', 'drouter-{ver}-docker.tar', 'Docker 镜像（169 MB）', False),",
     "        ('docker', 'drouter-{ver}-docker.tar', 'Docker 镜像（169 MB）'),",
     'ASSETS 非空且每个条目是 4 元组'),

    # 把整个核对块删掉 —— 等于回到「下载完才发现没有这个架构」。
    ('A6 下载前的 SHA256SUMS 名单核对被删',
     UPDATE,
     "    if target.get('arch_specific'):\n"
     "        _manifest = _fetch_manifest(ver)\n"
     "        if _manifest and target['name'] not in _manifest:\n"
     "            return {'ok': False,\n"
     "                    'msg_cn': '该版本没有发布本机架构（%s）的离线包：%s。'\n"
     "                              'deb 包是 Architecture: all，各 CPU 通用，'\n"
     "                              '请改用「Deb 安装包」这一项更新。'\n"
     "                              % (_deb_arch(), target['name'])}\n",
     "",
     '带架构后缀的附件在下载前用 SHA256SUMS 核对存在性'),

    # ---------------- B. 固件探测 ----------------
    # 删掉设备树分支 —— ARM 板重新落到 else，显示「传统 BIOS」。
    ('B1 删掉设备树分支（ARM 板重新误报传统 BIOS）',
     HELPER,
     "    elif has_dt:\n"
     "        firmware, fw_type = '设备树 (Device Tree)', 'dt'\n",
     "",
     '设备树分支返回稳定枚举 fw_type=dt'),

    # 枚举值不是小写 dt —— 前端拿它做分支会失配。
    ('B2 设备树枚举值写成大写 DT（前端分支失配）',
     HELPER,
     "        firmware, fw_type = '设备树 (Device Tree)', 'dt'",
     "        firmware, fw_type = '设备树 (Device Tree)', 'DT'",
     '设备树分支返回稳定枚举 fw_type=dt'),

    # 顺序反了：设备树优先于 EFI —— 树莓派 4 以 EFI 启动会被认成设备树。
    ('B3 判据顺序改成设备树优先于 EFI',
     HELPER,
     "    if is_efi:\n"
     "        firmware, fw_type = 'UEFI', 'efi'\n"
     "    elif has_dt:\n"
     "        firmware, fw_type = '设备树 (Device Tree)', 'dt'\n",
     "    if has_dt:\n"
     "        firmware, fw_type = '设备树 (Device Tree)', 'dt'\n"
     "    elif is_efi:\n"
     "        firmware, fw_type = 'UEFI', 'efi'\n",
     '判据顺序 EFI 优先于设备树'),

    # compatible 是一串 \0 分隔的标识，不切开会把整串塞给前端。
    ('B4 _dt_read 不再按 NUL 切开（拿整串当板名）',
     HELPER,
     "        for part in raw.split(b'\\0'):",
     "        for part in [raw]:",
     '_dt_read 按 NUL 切开取第一条'),

    # 兜底删掉 → ARM 板「厂商」一格空白，用户以为探测坏了。
    ('B5 去掉设备树板名兜底（厂商格留空）',
     HELPER,
     "    if not vendor:\n        vendor = dt_model\n",
     "",
     '无 DMI 厂商时用设备树板名兜底'),

    # 改字段名会让概览页那一行直接空掉（前端读 fw_type）。
    ('B6 fw_type 字段改名（前端读不到）',
     HELPER,
     "        'fw_type': fw_type,",
     "        'fwtype': fw_type,",
     '_read_bios 返回值里有 fw_type 枚举'),

    # ---------------- C. i18n ----------------
    # 词条没登记 → 英文界面这一格直接漏中文。
    ('C1 i18n 漏登记「设备树 (Device Tree)」',
     I18N,
     "        '设备树 (Device Tree)':\n"
     "          { 'n': 'Device Tree',\n"
     "            'd': 'Device Tree / U-Boot boot. ARM and RISC-V boards have "
     "no BIOS: the bootloader hands the kernel a device tree describing the "
     "hardware.' },\n",
     "",
     'i18n 的 SYSINFO_NOTES 里登记了'),

    # 只有 n 没有 d → 面板里那段英文说明是空的。
    ('C2 词条只给了 n 没给 d（说明落空）',
     I18N,
     "            'd': 'Device Tree / U-Boot boot. ARM and RISC-V boards have "
     "no BIOS: the bootloader hands the kernel a device tree describing the "
     "hardware.' },",
     "            'x': 'unused' },",
     '该词条同时给了 n / d 两段英文'),

    # ---------------- D. 打包脚本 ----------------
    ('D1 离线包名写死 amd64',
     OFFLINE,
     'NAME="drouter-${VERSION}-offline-${ARCH}"',
     'NAME="drouter-${VERSION}-offline-amd64"',
     '离线包名用 dpkg 架构变量拼'),

    ('D2 包内 README 目标机描述写死 amd64',
     OFFLINE,
     '**适用场景**：目标机（Debian 13 / ${ARCH}）**没有外网**，想一次装完。',
     '**适用场景**：目标机（Debian 13 (trixie) amd64）**没有外网**，想一次装完。',
     '包内离线安装说明里的目标机描述用 ${ARCH}'),

    ('D3 Docker 构建不接受第 2 个架构参数',
     DOCKER,
     'ARCH="${2:-$DPKG_NATIVE}"',
     'ARCH="$DPKG_NATIVE"',
     'Docker 构建接受第 2 个架构参数'),

    ('D4 交叉构建不传 --platform',
     DOCKER,
     '  PLATFORM_ARG=(--platform "linux/$ARCH")',
     '  PLATFORM_ARG=()',
     '交叉构建时传 --platform linux/<arch>'),

    ('D5 交叉构建镜像标签不带架构后缀',
     DOCKER,
     '  IMGTAG="$TAG-$ARCH"',
     '  IMGTAG="$TAG"',
     '交叉构建时镜像标签带 -<arch> 后缀'),

    ('D6 docker build 没接 PLATFORM_ARG',
     DOCKER,
     'docker build "${PLATFORM_ARG[@]}" -f',
     'docker build -f',
     'Docker 构建真的把隐含架构参数传给 docker build'),
]


def run():
    r = subprocess.run([PY, JUDGE], capture_output=True, cwd=ROOT)
    return r.returncode, (r.stdout or b'').decode('utf-8', 'replace')


def main():
    print('基线（应全绿）：')
    rc, out = run()
    tail = out.strip().splitlines()[-1] if out.strip() else ''
    print('  rc=%d  %s' % (rc, tail))
    if rc != 0:
        print('✘ 基线就是红的，先修好再谈反向验证')
        print(out[-2000:])
        return 1

    bad = []
    for desc, rel, old, new, kw in CASES:
        p = os.path.join(ROOT, rel)
        with open(p, 'rb') as f:
            orig = f.read().decode('utf-8')
        if old not in orig:
            print('  跳过 %-52s （锚点未命中）' % desc)
            bad.append(desc + ' 锚点未命中')
            continue
        try:
            with open(p, 'wb') as f:
                f.write(orig.replace(old, new, 1).encode('utf-8'))
            rc, out = run()
            caused = (rc != 0) and (kw in out)
            if caused:
                print('  ok   %-52s → 如约变红' % desc)
            else:
                print('  FAIL %-52s → rc=%d 关键词命中=%s'
                      % (desc, rc, kw in out))
                bad.append(desc)
        finally:
            with open(p, 'wb') as f:
                f.write(orig.encode('utf-8'))

    rc, out = run()
    tail = out.strip().splitlines()[-1] if out.strip() else ''
    print()
    print('还原后基线：rc=%d  %s' % (rc, tail))
    if rc != 0:
        bad.append('还原后未恢复全绿')
        print(out[-1500:])

    print()
    print('反向验证 %d 例，异常 %d 例' % (len(CASES), len(bad)))
    for b in bad:
        print('  ! %s' % b)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
