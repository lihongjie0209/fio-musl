#!/usr/bin/env python3
"""
fio 磁盘性能压测脚本

用法:
    python3 fio-bench.py <fio路径> <磁盘路径>               # 直接输出报告
    python3 fio-bench.py <fio路径> <磁盘路径> -o report.md  # 保存到文件

示例:
    python3 fio-bench.py ./fio-x86_64 /tmp/fio-test
    python3 fio-bench.py /usr/bin/fio /mnt/data/bench -o report.md -t 60
"""

import sys
import os
import subprocess
import json
from datetime import datetime


# ============================================================
# 配置
# ============================================================

FIO_IOENGINE = "libaio"

FIO_SIZE = "10G"   # 测试文件大小

TESTS = [
    # (名称, 读写模式, 块大小, 队列深度, 额外参数)
    ("顺序读",          "read",      "1M",  64,  ""),
    ("顺序写",          "write",     "1M",  64,  ""),
    ("顺序读 (单队列)",  "read",     "1M",   1,  ""),
    ("顺序写 (单队列)",  "write",    "1M",   1,  ""),
    ("随机读",          "randread",  "4K",  32,  ""),
    ("随机写",          "randwrite", "4K",  32,  ""),
    ("混合读写 70/30",   "randrw",   "4K",  32,  "--rwmixread=70"),
    ("深度随机读",       "randread",  "4K", 128,  ""),
]


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    sys.stderr.write(f"\033[0;34m>>\033[0m {msg}\n")
    sys.stderr.flush()

def ok(msg):
    sys.stderr.write(f"\033[0;32mOK\033[0m {msg}\n")
    sys.stderr.flush()

def warn(msg):
    sys.stderr.write(f"\033[0;33m!!\033[0m {msg}\n")
    sys.stderr.flush()

def err(msg):
    sys.stderr.write(f"\033[0;31mXX\033[0m {msg}\n")
    sys.stderr.flush()


def run_cmd(cmd):
    """运行命令并返回 (returncode, stdout, stderr)"""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err_out = p.communicate()
        return p.returncode, out.decode(errors="replace"), err_out.decode(errors="replace")
    except Exception as e:
        return -1, "", str(e)


# ============================================================
# 核心函数
# ============================================================

def detect_target(path):
    """检测测试路径所在的文件系统信息"""
    info = {"path": path}

    if os.path.islink(path):
        path = os.path.realpath(path)

    d = os.path.dirname(os.path.abspath(path))
    rc, out, _ = run_cmd(["df", "-T", d])
    if rc == 0:
        parts = out.strip().split("\n")
        if len(parts) >= 2:
            cols = parts[1].split()
            if len(cols) >= 2:
                info["device"] = cols[0]
                info["fs_type"] = cols[1]

    return info


def run_fio(fio_bin, disk_path, name, rw, bs, iodepth, runtime, extra=""):
    """运行单项 fio 测试，返回 dict 或 None"""
    is_mix = (rw == "randrw")

    cmd = [
        fio_bin,
        f"--name={name}", f"--ioengine={FIO_IOENGINE}",
        f"--rw={rw}", f"--bs={bs}", f"--size={FIO_SIZE}",
        f"--iodepth={iodepth}", f"--runtime={runtime}",
        "--time_based", "--direct=1",
        "--randrepeat=0", "--refill_buffers", "--norandommap",
        f"--filename={disk_path}",
    ]
    if is_mix and extra:
        cmd.append(extra)
    cmd.append("--output-format=json")

    sys.stderr.write("    ")
    sys.stderr.flush()

    rc, out, stderr_out = run_cmd(cmd)
    if rc != 0:
        err(f"fio 执行失败 (rc={rc}): {stderr_out[:200]}")
        return None

    try:
        data = json.loads(out)
    except ValueError as e:
        err(f"JSON 解析失败: {e}")
        return None

    try:
        job = data["jobs"][0]
        if is_mix:
            r = job["read"]
            w = job["write"]
            return {
                "bw": r["bw_mean"] / 1024, "iops": r["iops_mean"], "lat": r["clat_ns"]["mean"] / 1000,
                "bw_w": w["bw_mean"] / 1024, "iops_w": w["iops_mean"], "lat_w": w["clat_ns"]["mean"] / 1000,
            }
        elif rw in ("read", "randread"):
            r = job["read"]
            return {"bw": r["bw_mean"] / 1024, "iops": r["iops_mean"], "lat": r["clat_ns"]["mean"] / 1000}
        else:
            w = job["write"]
            return {"bw": w["bw_mean"] / 1024, "iops": w["iops_mean"], "lat": w["clat_ns"]["mean"] / 1000}
    except (KeyError, IndexError) as e:
        err(f"结果解析失败: {e}")
        return None


# ============================================================
# 报告生成
# ============================================================

def format_result(r, rw):
    """格式化单行结果"""
    if r is None:
        return "| - | - | - |"
    if rw == "randrw":
        if "读" in rw:
            return f"| {r['bw']:.0f} MiB/s | {r['iops']:.0f} | {r['lat']:.0f} μs |"
        return f"| {r['bw_w']:.0f} MiB/s | {r['iops_w']:.0f} | {r['lat_w']:.0f} μs |"
    return f"| {r['bw']:.0f} MiB/s | {r['iops']:.0f} | {r['lat']:.0f} μs |"


def generate_report(target_info, results, fio_ver, runtime):
    """生成 Markdown 报告"""
    uname = os.uname()
    cpu = os.sysconf("SC_NPROCESSORS_ONLN")

    mem = "N/A"
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    mem = f"{kb / 1024 / 1024:.0f}G"
                    break
    except:
        pass

    lines = [
        "# 磁盘性能测试报告",
        "",
        "## 环境信息",
        "",
        "| 项目 | 值 |",
        "|---|------|",
        f"| **fio 版本** | {fio_ver} |",
        f"| **测试目标** | {target_info['path']} |",
    ]
    if "fs_type" in target_info:
        lines.append(f"| **文件系统** | {target_info['fs_type']} |")
    if "device" in target_info:
        lines.append(f"| **设备** | {target_info['device']} |")
    lines += [
        f"| **主机** | {uname[1]} |",
        f"| **内核** | {uname[2]} |",
        f"| **CPU** | {cpu} 核 |",
        f"| **内存** | {mem} |",
        f"| **测试大小** | {FIO_SIZE} |",
        f"| **每项时长** | {runtime}s |",
        f"| **总测试数** | {len(TESTS)} 项 |",
        f"| **测试时间** | {datetime.now():%Y-%m-%d %H:%M:%S} |",
        "",
        "## 测试结果",
        "",
        "| # | 测试项 | 块大小 | 队列深度 | 带宽 | IOPS | 平均延迟 |",
        "|---|--------|--------|----------|------|------|----------|",
    ]

    for i, (name, rw, bs, qd, extra) in enumerate(TESTS):
        r = results[i]
        if r is None:
            lines.append(f"| {i+1} | {name} | {bs} | {qd} | - | - | - |")
        elif rw == "randrw":
            lines.append(f"| {i+1} | {name} (读) | {bs} | {qd} | {r['bw']:.0f} MiB/s | {r['iops']:.0f} | {r['lat']:.0f} μs |")
            lines.append(f"| {i+1} | {name} (写) | {bs} | {qd} | {r['bw_w']:.0f} MiB/s | {r['iops_w']:.0f} | {r['lat_w']:.0f} μs |")
        else:
            lines.append(f"| {i+1} | {name} | {bs} | {qd} | {r['bw']:.0f} MiB/s | {r['iops']:.0f} | {r['lat']:.0f} μs |")

    lines += [
        "",
        "## 延迟参考",
        "",
        "| 延迟范围 | 体验 | 典型场景 |",
        "|----------|------|----------|",
        "| < 100 μs | 优秀 | NVMe SSD |",
        "| 100 μs - 1 ms | 良好 | SATA SSD |",
        "| 1 ms - 10 ms | 一般 | 企业级 HDD |",
        "| > 10 ms | 较差 | 机械硬盘 / 高负载 |",
        "",
    ]
    return "\n".join(lines)


# ============================================================
# 主函数
# ============================================================

def main():
    args = sys.argv[1:]
    fio_bin = None
    disk_path = None
    output_file = None
    runtime = 30   # 默认每项 30 秒

    i = 0
    while i < len(args):
        if args[i] in ("-o", "--output"):
            i += 1
            if i < len(args):
                output_file = args[i]
        elif args[i] in ("-t", "--time"):
            i += 1
            if i < len(args):
                runtime = int(args[i])
        elif args[i].startswith("-"):
            sys.stderr.write(f"未知参数: {args[i]}\n")
            sys.exit(1)
        else:
            if fio_bin is None:
                fio_bin = args[i]
            elif disk_path is None:
                disk_path = args[i]
            else:
                sys.stderr.write(f"多余的参数: {args[i]}\n")
                sys.exit(1)
        i += 1

    if not fio_bin or not disk_path:
        sys.stderr.write("用法: python3 fio-bench.py <fio路径> <测试文件路径> [-t 秒数] [-o 输出文件]\n")
        sys.stderr.write("示例:\n")
        sys.stderr.write("  python3 fio-bench.py ./fio /tmp/fio-test                # 每项 30s，约 4 分钟\n")
        sys.stderr.write("  python3 fio-bench.py ./fio /mnt/data/bench -t 10        # 每项 10s，约 1.5 分钟\n")
        sys.stderr.write("  python3 fio-bench.py ./fio /tmp/fio-test -t 60 -o report.md\n")
        sys.exit(1)

    if not os.path.isfile(fio_bin):
        err(f"fio 文件不存在: {fio_bin}")
        sys.exit(1)
    if not os.access(fio_bin, os.X_OK):
        err(f"fio 不可执行: {fio_bin}")
        sys.exit(1)

    rc, ver_out, _ = run_cmd([fio_bin, "--version"])
    fio_ver = ver_out.strip() if rc == 0 else "unknown"

    log(f"fio {fio_ver} | 目标: {disk_path}")
    target_info = detect_target(disk_path)

    total_time = len(TESTS) * runtime
    log(f"开始测试 (共 {len(TESTS)} 项，每项 {runtime}s，预计 {total_time}s)...")
    results = []

    for idx, (name, rw, bs, qd, extra) in enumerate(TESTS):
        log(f"[{idx+1}/{len(TESTS)}] {name} ({bs} / iodepth={qd})")
        r = run_fio(fio_bin, disk_path, f"test-{idx}", rw, bs, qd, runtime, extra)
        results.append(r)
        if r is None:
            err(f"{name} 失败")
        elif rw == "randrw":
            ok(f"读 {r['bw']:.0f} MiB/s / {r['iops']:.0f} IOPS / {r['lat']:.0f} μs")
        else:
            ok(f"{r['bw']:.0f} MiB/s / {r['iops']:.0f} IOPS / {r['lat']:.0f} μs")

    log("生成报告...")
    report = generate_report(target_info, results, fio_ver, runtime)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        log(f"报告已保存: {output_file}")
    else:
        print(report)

    ok("完成")


if __name__ == "__main__":
    main()
