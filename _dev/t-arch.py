#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：架构无关化（arm64 适配）。

2026-10-16 做 arm64 Debian（Armbian 26.8.1 trixie / aarch64 / 6.18.35-ophub）
适配时，发现项目里有几处把 x86 当默认写死在字符串模板里 —— 在 amd64 上
看不出来，一到 arm64 就露馅。这个检查器把那几处钉死，防止改回去。

分四组：
  A. 更新模块（backend/drouter-update.py）—— 离线包资源名必须随本机架构变
  B. 固件探测（backend/drouter-helper.py）—— 设备树机器不能报「传统 BIOS」
  C. i18n 配套（web/i18n.js）—— 新增的展示串必须登记，否则英文界面漏中文
  D. 打包脚本（packaging/）—— 离线包说明与 Docker platform 支持

⚠️ 判据尽量查**语义/运行结果**，不要只查「某个名字出现过」。
   反向验证在 _dev/t-arch-inject.py（改写实现 → 本文件必须变红）。
"""
import ast
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)

fails = []
n = 0


def ck(desc, cond, extra=''):
    global n
    n += 1
    if not cond:
        fails.append('%s %s' % (desc, extra))
        print('FAIL  %s %s' % (desc, extra))
    else:
        print('ok    %s' % desc)


def read(p):
    with open(p, encoding='utf-8') as f:
        src = f.read()
    return src


UPDATE = read(P('backend', 'drouter-update.py'))
HELPER = read(P('backend', 'drouter-helper.py'))
I18N = read(P('web', 'i18n.js'))
OFFLINE = read(P('packaging', 'build-offline-bundle.sh'))
DOCKER = read(P('packaging', 'build-docker.sh'))

print('=' * 56)
print('A. 更新模块：附件名必须随架构变')
print('=' * 56)

# A1 —— 最直接的一条：模板里不许再出现具体的 amd64。
# 只查「资源名模板位置」（带 -offline- 的那串），避免把注释里的 amd64 也算进来。
ck('离线包资源名模板不含写死的 amd64',
   'offline-amd64' not in UPDATE,
   'offline-amd64 残留=%d 处' % UPDATE.count('offline-amd64'))
ck('离线包资源名模板不含写死的 arm64（应当用变量拼）',
   'offline-arm64' not in UPDATE)

# A2 —— 真的跑一遍：给不同架构必须拼出不同文件名。
# ⚠️ 这一条是**运行态**判据，比「代码里有没有 _deb_arch 这个字符串」强得多：
#    把 build_assets 里的 %s 换成写死的 amd64，拼出来立刻露馅。
spec = importlib.util.spec_from_file_location(
    'drouter_update_arch', P('backend', 'drouter-update.py'))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)

ASSETS = getattr(U, 'ASSETS', [])

names = {}
_load_err = ''
try:
    for arch in ('amd64', 'arm64', 'armhf'):
        d = {a['key']: a['name'] for a in U.asset_list('9.9.9', arch=arch)}
        names[arch] = d
except Exception as e:                                  # pragma: no cover
    _load_err = str(e)

ck('asset_list 支持按架构拼出清单（模块可加载）', not _load_err, _load_err)
if not _load_err:
    ck('arm64 拼出 -offline-arm64- 的离线包名',
       names['arm64']['offline'] == 'drouter-9.9.9-offline-arm64.tar.gz',
       names['arm64']['offline'])
    ck('amd64 拼出 -offline-amd64- 的离线包名',
       names['amd64']['offline'] == 'drouter-9.9.9-offline-amd64.tar.gz',
       names['amd64']['offline'])
    # 三个架构必须两两不同 —— 防止有人把 _deb_arch() 写成常量
    ck('不同架构拼出的离线包名两两不同',
       names['amd64']['offline'] != names['arm64']['offline']
       != names['armhf']['offline'] != names['amd64']['offline'])
    # deb 是 Architecture: all，**必须**三架构同名
    ck('deb 本体三架构同名（Architecture: all）',
       names['amd64']['deb'] == names['arm64']['deb'] == names['armhf']['deb'],
       names['arm64']['deb'])
    ck('只有离线包标了 arch_specific',
       [a['arch_specific'] for a in U.asset_list('9.9.9', arch='arm64')]
       == [False, True, False])

# A3 —— ASSETS 是四元组，缺一列会让 asset_list 解包炸 ValueError
# （表现是「更新」页整块渲染不出来）
ck('ASSETS 非空且每个条目是 4 元组',
   bool(ASSETS) and all(isinstance(a, tuple) and len(a) == 4 for a in ASSETS),
   str([len(a) for a in ASSETS]))

# A4 —— 下载前先用官方 SHA256SUMS 清单核对有没有这个架构的包。
# 没有这条，arm64 用户点「离线包更新」会先传几十 MB 才失败。
ck('带架构后缀的附件在下载前用 SHA256SUMS 核对存在性',
   re.search(r"target\.get\('arch_specific'\)", UPDATE)
   and re.search(r"_manifest\s*=\s*_fetch_manifest\(ver\)", UPDATE)
   and re.search(r"target\['name'\]\s+not\s+in\s+_manifest", UPDATE))
ck('清单核对放在起 worker 之前（不能下了才说没有）',
   UPDATE.index("_fetch_manifest(ver)") < UPDATE.index("_apply_worker(ver, target"),
   '核对位置在 worker 之后 = 白做完')

print()
print('=' * 56)
print('B. 固件探测：设备树机器不能报传统 BIOS')
print('=' * 56)

ck('_read_bios 存在设备树分支（第三种固件类型）',
   '设备树 (Device Tree)' in HELPER)
ck('设备树分支返回稳定枚举 fw_type=dt',
   re.search(r"firmware,\s*fw_type\s*=\s*'设备树 \(Device Tree\)',\s*'dt'", HELPER)
   is not None)
# 判据顺序：EFI 优先于设备树 —— 树莓派 4 既能 EFI 也能设备树，应当认 EFI
_i_dt_branch = HELPER.find("elif has_dt:")
_i_efi_branch = HELPER.find("if is_efi:")
ck('判据顺序 EFI 优先于设备树',
   _i_efi_branch >= 0 and _i_dt_branch >= 0 and _i_efi_branch < _i_dt_branch,
   'efi@%s dt@%s' % (_i_efi_branch, _i_dt_branch))
ck('设备树两个候选路径都查（/proc 与 /sys/firmware）',
   "'/proc/device-tree/model'" in HELPER
   and "'/sys/firmware/devicetree/base/model'" in HELPER)
# compatible 属性是多条 \0 结尾串拼的，不切开会把整串（含空字符）塞给前端
ck('_dt_read 按 NUL 切开取第一条（不是整串原样返回）',
   re.search(r"for part in raw\.split\(b'\\0'\):", HELPER) is not None)
ck('无 DMI 厂商时用设备树板名兜底（面板厂商格不留空）',
   re.search(r"if not vendor:\s*\n\s*vendor = dt_model", HELPER) is not None)
# 兼容：前端读的是 firmware / is_efi / vendor / version / date。
# 加字段没问题，**删掉或改名**会让概览页那一行直接空掉。
_bios_keys = set()
try:
    _tree = ast.parse(HELPER)
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.FunctionDef) and _node.name == '_read_bios':
            for _sub in ast.walk(_node):
                # 只认**最后的 return 字典**，别把中途别的字典也算进来
                if isinstance(_sub, ast.Return) and isinstance(_sub.value, ast.Dict):
                    for _k in _sub.value.keys:
                        if isinstance(_k, ast.Constant):
                            _bios_keys.add(_k.value)
except Exception as _e:                                 # pragma: no cover
    print('   （AST 解析异常：%s）' % _e)

ck('_read_bios 返回值里有 fw_type 枚举',
   'fw_type' in _bios_keys, str(sorted(_bios_keys)))
ck('_read_bios 仍返回前端在用的旧字段（firmware/is_efi/vendor/version/date）',
   {'firmware', 'is_efi', 'vendor', 'version', 'date'} <= _bios_keys,
   str(sorted({'firmware', 'is_efi', 'vendor', 'version', 'date'} - _bios_keys)))

print()
print('=' * 56)
print('C. i18n：新增展示串必须登记')
print('=' * 56)

_i = I18N.find("'SYSINFO_NOTES'")
_j = I18N.find("'PAGE_NOTES'", _i if _i > 0 else 0)
_block = I18N[_i:_j] if _i > 0 and _j > _i else ''
ck('i18n 的 SYSINFO_NOTES 里登记了「设备树 (Device Tree)」',
   "'设备树 (Device Tree)'" in _block)
# ⚠️ 必须把范围限定在**本词条的花括号内**。早先用 300 字符的松弛窗口找 'd':，
#    会跨到下一条词条（紧挨着的「未读取到温度传感器…」恰好带 'd':）→
#    删掉本词条的 d 之后判据照样绿。反向验证 C2 揪出来的假绿。
_m_dt = re.search(r"'设备树 \(Device Tree\)':\s*(\{.*?\})", _block, re.S)
_entry_dt = _m_dt.group(1) if _m_dt else ''
ck('该词条同时给了 n / d 两段英文',
   "'n': '" in _entry_dt and "'d': '" in _entry_dt,
   '词条块=%r' % (_entry_dt[:80],))
# 同一个 key 重复登记会让 JS 直接语法/语义冲突
ck('「设备树 (Device Tree)」只登记一次',
   I18N.count("'设备树 (Device Tree)'") == 1)

print()
print('=' * 56)
print('D. 打包脚本')
print('=' * 56)

ck('离线包名用 dpkg 架构变量拼',
   re.search(r'NAME="drouter-\$\{VERSION\}-offline-\$\{ARCH\}"', OFFLINE) is not None
   or 'drouter-${VERSION}-offline-${ARCH}' in OFFLINE)
# 包内 README 里的两处 amd64 已换成变量
ck('包内离线安装说明里的目标机描述用 ${ARCH}（不是写死 amd64）',
   'Debian 13 / ${ARCH}' in OFFLINE)
ck('包内离线安装说明的系统核对项提到 dpkg --print-architecture',
   'dpkg --print-architecture\\`' in OFFLINE
   or 'dpkg --print-architecture' in OFFLINE)
ck('包内说明不再出现「Debian 13 (trixie) amd64」的写死表述',
   'Debian 13 (trixie) amd64' not in OFFLINE)

ck('Docker 构建接受第 2 个架构参数',
   re.search(r'ARCH="\$\{2:-\$DPKG_NATIVE\}"', DOCKER) is not None)
ck('交叉构建时传 --platform linux/<arch>',
   re.search(r'PLATFORM_ARG=\(--platform "linux/\$ARCH"\)', DOCKER) is not None)
ck('交叉构建时镜像标签带 -<arch> 后缀',
   re.search(r'IMGTAG="\$TAG-\$ARCH"', DOCKER) is not None)
ck('原生构建时 IMGTAG 就是 TAG（不改动既有发版产物）',
   re.search(r'IMGTAG="\$TAG"\s*\n', DOCKER) is not None)
ck('Docker 构建真的把隐含架构参数传给 docker build',
   'docker build "${PLATFORM_ARG[@]}"' in DOCKER)

print()
print('=' * 56)
print('判据 %d 条，失败 %d 条' % (n, len(fails)))
if fails:
    for f in fails:
        print('  FAIL: %s' % f)
sys.exit(1 if fails else 0)
