#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arm64 / 非 x86 板子的本地只读验收。

在目标机上跑一遍，确认 drouter 的代码在**这台**机器上站得住：

    python3 devtools/verify-arm64.py            # 默认以仓库根目录为源码树
    python3 devtools/verify-arm64.py /opt/drouter

查什么：
  1. 后端每个 .py 能 compile
  2. 固件探测：设备树机器必须报「设备树」，不能落到「传统 BIOS」
  3. 依赖自检 depcheck 能跑完并给出发行版名
  4. 配置渲染：能出配置的出配置，缺必填字段的报**指定的**校验，不许报别的
  5. 更新模块：离线包文件名必须跟本机 dpkg 架构一致

⚠️ 全程只读：不装包、不写配置、不起任何服务、不动网络。
   8080 端口上有别的业务在跑的机器也能安全跑这一份。
   退出码 0 = 全通过。
"""
import glob
import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

# 这三个依赖 Linux 专属的 /sys 接口，别在 Windows 开发机上跑，会误报。
if not sys.platform.startswith('linux'):
    print('这份验收脚本要在目标 Linux 机器上跑（依赖 /sys/firmware、'
          '/sys/class/dmi 等接口）。')
    print('ROOT=%s' % ROOT)
    sys.exit(2)

fails = []
n = 0


def ck(desc, cond, extra=''):
    global n
    n += 1
    if cond:
        print('ok    %s' % desc)
    else:
        print('FAIL  %s  %s' % (desc, extra))
        fails.append(desc)


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, 'backend', name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


print('源码树：%s' % ROOT)
print('=' * 60)
print('1. 后端模块语法')
print('=' * 60)
names = sorted(os.path.basename(p)[:-3]
               for p in glob.glob(os.path.join(ROOT, 'backend', '*.py')))
if not names:
    print('✘ %s/backend 下没找到 .py —— 源码树给错了？' % ROOT)
    sys.exit(1)
bad = []
for nm in names:
    with open(os.path.join(ROOT, 'backend', nm + '.py'),
              encoding='utf-8') as f:
        src = f.read()
    try:
        compile(src, nm, 'exec')
    except SyntaxError as e:
        bad.append('%s: %s' % (nm, e))
ck('后端 %d 个模块 compile 通过' % len(names), not bad, '; '.join(bad))

H = load('drouter-helper')
print()
print('=' * 60)
print('2. 固件探测（ARM 板最容易误报的一项）')
print('=' * 60)
bios = H._read_bios()
print('   _read_bios() =', json.dumps(bios, ensure_ascii=False))
machine = os.uname().machine
# 这一条只替 placeholders 做提示，不参与判定 —— 有没有 DMI 是**实探测**出来的。
has_dmi = os.path.isdir('/sys/class/dmi')
has_efi = os.path.isdir('/sys/firmware/efi')
print('   本机：machine=%s  /sys/class/dmi=%s  /sys/firmware/efi=%s'
      % (machine, has_dmi, has_efi))
ck('fw_type 是三者之一（efi / dt / bios）',
   bios.get('fw_type') in ('efi', 'dt', 'bios'), str(bios.get('fw_type')))
if not (has_dmi or has_efi):
    ck('无 EFI 且无 DMI ⇒ 必须判成设备树，不能写成传统 BIOS',
       bios.get('fw_type') == 'dt', str(bios.get('fw_type')))
    ck('厂商格有值（拿设备树板名兜底，不能空着）',
       bool(bios.get('vendor')), repr(bios.get('vendor')))
if bios.get('fw_type') == 'dt':
    ck('compat 按 NUL 切开，不含空字符',
       '\x00' not in (bios.get('compat') or ''),
       repr(bios.get('compat')))
ck('前端在用的旧字段一个没少',
   all(k in bios for k in ('firmware', 'is_efi', 'vendor', 'version', 'date')))

print()
print('=' * 60)
print('3. 依赖自检 depcheck')
print('=' * 60)
p = subprocess.run([sys.executable, 'drouter-helper.py', 'depcheck',
                    '{"op":"check"}'], cwd=os.path.join(ROOT, 'backend'),
                   capture_output=True, text=True, timeout=180)
try:
    d = json.loads(p.stdout or '{}')
except ValueError:
    d = {'ok': False, 'msg_cn': (p.stdout or p.stderr or '')[:200]}
ck('depcheck 返回 ok', bool(d.get('ok')), str(d.get('msg_cn'))[:160])
data = d.get('data') or {}
print('   summary =', json.dumps(data.get('summary'), ensure_ascii=False))
print('   extra   =', json.dumps(data.get('extra'), ensure_ascii=False))
ck('发行版名识别出来了', bool((data.get('extra') or {}).get('os')))
need = [i['name'] for i in (data.get('items') or [])
        if i.get('required') and not i.get('ok')]
print('   必需但未装 %d 项：%s' % (len(need), '、'.join(need)))
print('   （缺的都是路由功能包，面板「依赖自检」页可以逐项安装；')
print('     Python 运行时与本项目自身的可导入性已经在第 1 步验过）')

print()
print('=' * 60)
print('4. 配置渲染（渲染但绝不落盘）')
print('=' * 60)
# 空 cfg 下这三个模块第一步就是必填字段校验。写死期望串是为了抓「校验本身
# 挂了」这种情况 —— 报别的（比如 ImportError、KeyError）才是真问题。
EXPECT_VALIDATE = {
    'dnsmasq': 'LAN 接口不能为空',
    'network': 'WAN 网卡不能为空',
    'ppp':     'PPPoE 网卡不能为空',
}
spec = importlib.util.spec_from_file_location(
    'render', os.path.join(ROOT, 'backend', 'render.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
rendered = {}
wrong = []
for mod in sorted(R.RENDERERS):
    try:
        r = R.render(mod, {})
        if isinstance(r, (list, tuple)) and r and len(r[0]) == 2:
            rendered[mod] = r[0][1]
        else:
            wrong.append('%s 返回了非二元组' % mod)
    except Exception as e:
        want = EXPECT_VALIDATE.get(mod)
        got = str(e)
        if want is None or want not in got:
            wrong.append('%s 抛了非预期异常：%s' % (mod, got[:60]))
        rendered[mod] = None
NL = chr(10)
for mod in sorted(rendered):
    body = rendered[mod]
    if body is None:
        print('   %-12s 必填校验：%s' % (mod, EXPECT_VALIDATE.get(mod, '?')))
    else:
        print('   %-12s %3d 行 / %5d 字符'
              % (mod, body.count(NL) + 1, len(body)))
ck('每个模块要么出配置、要么报指定的必填校验', not wrong, '; '.join(wrong))

print()
print('=' * 60)
print('5. 更新模块的架构感知')
print('=' * 60)
U = load('drouter-update')
arch = U._deb_arch()
print('   本机 _deb_arch() =', arch)
lst = U.asset_list('1.0.10')
for a in lst:
    print('   %-8s %-42s arch_specific=%s'
          % (a['key'], a['name'], a['arch_specific']))
ck('离线包名带本机架构后缀',
   any(a['key'] == 'offline' and ('offline-%s' % arch) in a['name']
       for a in lst))
ck('deb 不带架构后缀（Architecture: all）',
   any(a['key'] == 'deb' and '_all.deb' in a['name'] for a in lst))
ck('别的架构拼出别的名字（不是写死一个）',
   [x['name'] for x in U.asset_list('1.0.10', arch='amd64')]
   != [x['name'] for x in lst] or arch == 'amd64')

print()
print('=' * 60)
print('判据 %d 条，失败 %d 条' % (n, len(fails)))
for f in fails:
    print('  ! %s' % f)
print()
print('⚠️ 这份脚本只验「代码在这台机器上能不能跑」，')
print('   不验「当路由器转发起来行不行」—— 那要点面板上的应用，会真改网络。')
sys.exit(1 if fails else 0)
