#!/usr/bin/env python3
"""
ESP Surface Visualization GUI v1.0 (PyQt5)
Multiwfn (fchk -> surface data) + VMD (Preview + Tachyon Rendering)
PyQt5 version of esp_surface_gui.py

Author: Workbuddy
Based on: sobereva.com/443 (Multiwfn+VMD ESP surface tutorial)

Features:
  - Select .fch/.fchk files (single or batch)
  - Mode: PT (vertex-colored), ISO (isosurface-colored), EXT (extrema), ALL (overlay)
  - Color scale range adjustment
  - VMD interactive preview or headless Tachyon render
  - Output path selection

Usage:
  Run directly -> GUI pops up
  python esp_surface_gui_qt.py
"""

import os
import sys
import re
import glob
import subprocess
import threading
import shutil
import time
import tempfile
import socket
import configparser

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QListWidget, QRadioButton,
    QCheckBox, QComboBox, QProgressBar, QPlainTextEdit, QFileDialog,
    QMessageBox, QDialog, QButtonGroup, QSizePolicy, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSlider
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette

# ── Optional dependencies ──────────────────────────────────
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import numpy as np
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

# ── Default Paths ────────────────────────────────────────
DEFAULT_MULTIWFN = r"E:\Multiwfn_2026.4.10_bin_Win64\Multiwfn.exe"
DEFAULT_VMD = r"C:\Program Files (x86)\University of Illinois\VMD\vmd.exe"
DEFAULT_TACHYON = r"C:\Program Files (x86)\University of Illinois\VMD\tachyon_WIN32.exe"

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__)),
    "esp_surface_gui.ini"
)


def load_config():
    """Read path configuration from ini file."""
    cfg = configparser.ConfigParser()
    paths = {
        "multiwfn": DEFAULT_MULTIWFN,
        "vmd": DEFAULT_VMD,
        "vmd_dir": os.path.dirname(DEFAULT_VMD),
        "tachyon": DEFAULT_TACHYON,
    }
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE, encoding="utf-8")
        if "paths" in cfg:
            for k in paths:
                if k in cfg["paths"] and cfg["paths"][k]:
                    paths[k] = cfg["paths"][k]
    return paths


def save_config(multiwfn, vmd, vmd_dir, tachyon=""):
    """Save path configuration to ini file."""
    cfg = configparser.ConfigParser()
    cfg["paths"] = {
        "multiwfn": multiwfn,
        "vmd": vmd,
        "vmd_dir": vmd_dir,
        "tachyon": tachyon or DEFAULT_TACHYON,
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


# ── Multiwfn Command Templates ───────────────────────────
CMD_ESPPT = """
12
3
0.15
0
5
mol.pdb
6
"""

CMD_ESPISO = """
5
1
3
2
0
5
12
1
2
"""

CMD_ESPEXT = """
12
3
0.15
0
5
mol.pdb
6
2
"""

CMD_ESP_AREA_DIST = """
12
0
9
all
"""

# ── Progress Parsing ──────────────────────────────────────
_PROGRESS_PATTERNS = [
    (re.compile(r'(\d+(?:\.\d+)?)\s*%', re.IGNORECASE), 'pct'),
    (re.compile(r'(\d+)\s*/\s*(\d+)', re.IGNORECASE), 'frac'),
    (re.compile(r'(\d+)\s+of\s+(\d+)', re.IGNORECASE), 'frac'),
]


def _parse_progress(line):
    """Try to extract progress info from a Multiwfn output line."""
    for pat, kind in _PROGRESS_PATTERNS:
        m = pat.search(line)
        if m:
            if kind == 'pct':
                return min(float(m.group(1)) / 100.0, 1.0), line.strip()
            elif kind == 'frac':
                num, den = int(m.group(1)), int(m.group(2))
                if den > 0:
                    return min(num / den, 1.0), line.strip()
    return None, line.strip()


def run_multiwfn(multiwfn_exe, fch_path, cmd_string, work_dir, extra_args="",
                 progress_cb=None):
    """Run Multiwfn with command string via file redirection."""
    fch_name = os.path.basename(fch_path)

    cmd_file = os.path.join(work_dir, "_mw_cmd.txt")
    if not cmd_string.startswith("\n"):
        cmd_string = "\n" + cmd_string
    with open(cmd_file, "w", encoding="ascii") as f:
        f.write(cmd_string)

    if extra_args:
        cmd = f'"{multiwfn_exe}" "{fch_name}" {extra_args} < _mw_cmd.txt'
    else:
        cmd = f'"{multiwfn_exe}" "{fch_name}" < _mw_cmd.txt'

    try:
        proc = subprocess.Popen(
            cmd, shell=True,
            cwd=work_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except Exception:
        return False, ""

    stdout_lines = []
    try:
        for line in proc.stdout:
            stdout_lines.append(line)
            if progress_cb:
                try:
                    progress_cb(line.rstrip())
                except Exception:
                    pass
    except Exception:
        pass

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    full_stdout = "".join(stdout_lines)

    if proc.returncode not in (0, 24):
        return False, full_stdout

    return True, full_stdout


def _read_original_vmd(multiwfn_exe, vmd_name):
    """Read original .vmd file from Multiwfn examples/drawESP directory."""
    mwfn_dir = os.path.dirname(multiwfn_exe)
    orig_path = os.path.join(mwfn_dir, "examples", "drawESP", vmd_name)
    if os.path.exists(orig_path):
        with open(orig_path, "r", encoding="utf-8") as f:
            return f.read()
    orig_path2 = os.path.join(mwfn_dir, "..", "examples", "drawESP", vmd_name)
    orig_path2 = os.path.abspath(orig_path2)
    if os.path.exists(orig_path2):
        with open(orig_path2, "r", encoding="utf-8") as f:
            return f.read()
    return None


def generate_vmd_script_pt(multiwfn_exe, vmd_dir, nsystem=1, color_low=-50.0,
                           color_high=50.0, pt_size=4.0):
    orig = _read_original_vmd(multiwfn_exe, "ESPpt.vmd")
    if orig:
        tcl = orig
        tcl = tcl.replace("set nsystem 1", f"set nsystem {nsystem}")
        tcl = tcl.replace("set colorlow -50", f"set colorlow {color_low}")
        tcl = tcl.replace("set colorhigh 50", f"set colorhigh {color_high}")
        tcl = tcl.replace("set ptsize 4.0", f"set ptsize {pt_size}")
    else:
        tcl = f"""#This script is used to draw ESP colored surface vertices
color scale method BWR
color Display Background white
axes location Off
display depthcue off
display rendermode Normal

set nsystem {nsystem}
set colorlow {color_low}
set colorhigh {color_high}
set ptsize {pt_size}

for {{set i 1}} {{$i<=$nsystem}} {{incr i}} {{
mol new mol$i.pdb
mol new vtx$i.pdb
mol modstyle 0 [expr 2*($i-1)] CPK 1.000000 0.300000 22.000000 22.000000
mol modcolor 0 [expr 2*($i-1)+1] Beta
mol modstyle 0 [expr 2*($i-1)+1] Points $ptsize
mol scaleminmax [expr 2*($i-1)+1] 0 $colorlow $colorhigh
}}
"""
    script_path = os.path.join(vmd_dir, "esp_auto_pt.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystem=1, color_low=-0.03,
                            color_high=0.03):
    orig = _read_original_vmd(multiwfn_exe, "ESPiso.vmd")
    if orig:
        tcl = orig
        tcl = tcl.replace("set nsystem 1", f"set nsystem {nsystem}")
        tcl = tcl.replace("set colorlow -0.03", f"set colorlow {color_low}")
        tcl = tcl.replace("set colorhigh 0.03", f"set colorhigh {color_high}")
        tcl = tcl.replace("set colorlow -0.8", f"#set colorlow -0.8")
        tcl = tcl.replace("set colorhigh 0.8", f"#set colorhigh 0.8")
    else:
        tcl = f"""#This script is used to draw ESP colored molecular vdW surface (rho=0.001)
color scale method BWR
color Display Background white
axes location Off
display depthcue off
display rendermode GLSL
light 2 on
light 3 on
material change transmode EdgyGlass 1.0
material change specular EdgyGlass 0.15
material change shininess EdgyGlass 0.95
material change opacity EdgyGlass 0.7
material change outlinewidth EdgyGlass 0.9
material change outline EdgyGlass 0.5

set nsystem {nsystem}
set colorlow {color_low}
set colorhigh {color_high}

puts "Current working directory: [pwd]"
puts "Looking for cube files..."

for {{set i 1}} {{$i<=$nsystem}} {{incr i}} {{
    if {{[file exists density$i.cub]}} {{
        puts "Found density$i.cub"
    }} else {{
        puts "WARNING: density$i.cub not found!"
    }}
    if {{[file exists ESP$i.cub]}} {{
        puts "Found ESP$i.cub"
    }} else {{
        puts "WARNING: ESP$i.cub not found!"
    }}
    
    mol new density$i.cub
    mol addfile ESP$i.cub
    set id [molinfo top get id]
    puts "Loaded molecule $i with ID $id"
    
    mol modstyle 0 $id CPK 1.000000 0.300000 22.000000 22.000000
    mol addrep $id
    mol modstyle 1 $id Isosurface 0.001000 0 0 0 1 1
    mol modmaterial 1 $id EdgyGlass
    mol modcolor 1 $id Volume 1
    mol scaleminmax $id 1 $colorlow $colorhigh
    display resetview
    puts "System $i configured successfully"
}}

puts "All systems loaded."
display resetview
"""
    script_path = os.path.join(vmd_dir, "esp_auto_iso.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_ext(multiwfn_exe, vmd_dir):
    orig = _read_original_vmd(multiwfn_exe, "ESPext.vmd")
    if orig:
        tcl = orig
    else:
        tcl = """#Load surfanalysis.pdb to show ESP extrema on vdW surface

mol new surfanalysis.pdb
mol modstyle 0 top VDW 0.07 20
mol modselect 0 top name C
mol modcolor 0 top ColorID 32
mol addrep top
mol modstyle 1 top VDW 0.07 20
mol modselect 1 top name O
mol modcolor 1 top ColorID 21
"""
    script_path = os.path.join(vmd_dir, "esp_auto_ext.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_all(multiwfn_exe, vmd_dir, nsystem=1,
                            color_low_iso=-0.03, color_high_iso=0.03):
    iso_tcl = _read_original_vmd(multiwfn_exe, "ESPiso.vmd")
    if iso_tcl:
        iso_tcl = iso_tcl.replace("set nsystem 1", f"set nsystem {nsystem}")
        iso_tcl = iso_tcl.replace("set colorlow -0.03", f"set colorlow {color_low_iso}")
        iso_tcl = iso_tcl.replace("set colorhigh 0.03", f"set colorhigh {color_high_iso}")
        iso_tcl = iso_tcl.replace("set colorlow -0.8", f"#set colorlow -0.8")
        iso_tcl = iso_tcl.replace("set colorhigh 0.8", f"#set colorhigh 0.8")
    else:
        iso_tcl = f"""color scale method BWR
color Display Background white
axes location Off
display depthcue off
display rendermode GLSL
light 2 on
light 3 on
material change transmode EdgyGlass 1.0
material change specular EdgyGlass 0.15
material change shininess EdgyGlass 0.95
material change opacity EdgyGlass 0.7
material change outlinewidth EdgyGlass 0.9
material change outline EdgyGlass 0.5

set nsystem {nsystem}
set colorlow {color_low_iso}
set colorhigh {color_high_iso}

for {{set i 1}} {{$i<=$nsystem}} {{incr i}} {{
set id [expr $i-1]
mol new density$i.cub
mol addfile ESP$i.cub
mol modstyle 0 $id CPK 1.000000 0.300000 22.000000 22.000000
mol addrep $id
mol modstyle 1 $id Isosurface 0.001000 0 0 0 1 1
mol modmaterial 1 $id EdgyGlass
mol modcolor 1 $id Volume 1
mol scaleminmax $id 1 $colorlow $colorhigh
}}

"""

    ext_tcl = _read_original_vmd(multiwfn_exe, "ESPext.vmd")
    if not ext_tcl:
        ext_tcl = """#Load surfanalysis.pdb to show ESP extrema on vdW surface

mol new surfanalysis.pdb
mol modstyle 0 top VDW 0.07 20
mol modselect 0 top name C
mol modcolor 0 top ColorID 32
mol addrep top
mol modstyle 1 top VDW 0.07 20
mol modselect 1 top name O
mol modcolor 1 top ColorID 21
"""

    tcl = iso_tcl.rstrip() + "\n\n# ── EXT: ESP extrema points ──\n" + ext_tcl

    script_path = os.path.join(vmd_dir, "esp_auto_all.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


# ── ESP Area Distribution Analysis ─────────────────────────

def run_multiwfn_area_dist(multiwfn_exe, fch_path, work_dir,
                            range_low=-25.0, range_high=22.0,
                            n_bins=15, unit_code=3, progress_cb=None,
                            log_cb=None):
    cmd = (
        CMD_ESP_AREA_DIST
        + f"{range_low},{range_high}\n{n_bins}\n{unit_code}\nn\n"
    )
    ok, stdout = run_multiwfn(
        multiwfn_exe, fch_path, cmd, work_dir, progress_cb=progress_cb
    )
    data = _parse_area_output(stdout)
    if not data:
        tail = stdout.strip().split("\n")[-30:]
        dbg = ["  [AreaDist] No data parsed. Last 30 lines of Multiwfn stdout:"]
        for i, line in enumerate(tail):
            dbg.append(f"    {i:3d}: {line}")
        dbg_str = "\n".join(dbg)
        print(dbg_str)
        if log_cb:
            log_cb(dbg_str)
    return ok, data


def _parse_area_output(stdout):
    data = []
    lines = stdout.split("\n")

    ncols = 3
    header_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if 'Begin' in stripped and 'End' in stripped and 'Center' in stripped:
            ncols = 5
            header_idx = i
            break
        if 'Center' in stripped and 'Area' in stripped and 'Begin' not in stripped:
            ncols = 3
            header_idx = i
            break

    if header_idx < 0:
        return data

    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            if data:
                break
            continue
        if stripped.startswith('Sum') or stripped.startswith('---') or \
           stripped.startswith('===') or 'Area unit' in stripped:
            continue

        nums = re.findall(r'[-+]?\d+\.?\d*', stripped)
        if ncols == 5:
            if len(nums) >= 5:
                try:
                    center = float(nums[2])
                    area = float(nums[3])
                    pct = float(nums[4])
                    data.append((center, area, pct))
                except ValueError:
                    continue
            elif len(nums) == 2:
                continue
        else:
            if len(nums) >= 3:
                try:
                    c = float(nums[0])
                    a = float(nums[1])
                    p = float(nums[2])
                    data.append((c, a, p))
                except ValueError:
                    continue

    return data


def plot_esp_area_histogram(area_data, output_path,
                             title="ESP Area Distribution",
                             xlabel="ESP (kcal/mol)"):
    if not _HAS_MPL:
        print("  matplotlib not available, cannot generate chart")
        return None

    if not area_data:
        print("  No area data to plot")
        return None

    centers = [d[0] for d in area_data]
    areas = [d[1] for d in area_data]

    if len(centers) > 1:
        bin_width = centers[1] - centers[0]
    else:
        bin_width = 2.0

    data_min, data_max = min(centers), max(centers)
    pad = max(0.5, (data_max - data_min) * 0.05)
    norm = mcolors.TwoSlopeNorm(vmin=data_min - pad, vcenter=0, vmax=data_max + pad)
    cmap_bwr = plt.get_cmap('bwr')
    colors = [cmap_bwr(norm(c)) for c in centers]

    fig, ax = plt.subplots(figsize=(10, 5.5))

    bars = ax.bar(
        centers, areas, width=bin_width * 0.92, align='center',
        color=colors, edgecolor='#333333', linewidth=0.8,
    )

    ax.set_xlabel(xlabel, fontsize=12)
    ax.set_ylabel('Surface Area (Å²)', fontsize=12)
    ax.set_title(title, fontsize=13, fontweight='bold')

    ax.set_xticks(centers)
    ax.set_xticklabels([f'{c:.1f}' for c in centers],
                       rotation=45, ha='right', fontsize=8)

    max_area = max(areas) if max(areas) > 0 else 1
    for bar, val in zip(bars, areas):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2.,
                bar.get_height() + max_area * 0.02,
                f'{val:.1f}', ha='center', va='bottom',
                fontsize=7, rotation=90, color='#333333',
            )

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlim(centers[0] - bin_width, centers[-1] + bin_width)
    ax.set_ylim(0, max_area * 1.18)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)

    return output_path


def save_area_data_file(area_data, output_path):
    if not area_data:
        print("  No area data to export")
        return None

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("Center(kcal/mol)\tArea(Ang^2)\tPercentage(%)\n")
            for center, area, pct in area_data:
                f.write(f"{center:.4f}\t{area:.4f}\t{pct:.4f}\n")
        return output_path
    except OSError as e:
        print(f"  Failed to write data file: {e}")
        return None


# ── VMD Functions ──────────────────────────────────────────

def launch_vmd(vmd_exe, script_path, vmd_dir):
    cmd = f'"{vmd_exe}" -e "{script_path}"'
    print(f"  Launching VMD (cwd={vmd_dir})...")
    proc = subprocess.Popen(cmd, shell=True, cwd=vmd_dir)
    print(f"  VMD PID   : {proc.pid}")
    return proc


def copy_to_vmd_dir(src_dir, pattern, vmd_dir):
    copied = []
    for f in glob.glob(os.path.join(src_dir, pattern)):
        dest = os.path.join(vmd_dir, os.path.basename(f))
        shutil.copy2(f, dest)
        copied.append(dest)
    return copied


def find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def socket_tcl_snippet(port, callback_port=0):
    pick_code = ""
    if callback_port > 0:
        pick_code = (
            "\n# === Pick callback: send clicked atom info to Python GUI ===\n"
            "trace add variable ::vmd_pick_event write _esp_pick_cb\n"
            "proc _esp_pick_cb {args} {\n"
            "    global vmd_pick_atom vmd_pick_mol\n"
            "    set mol $vmd_pick_mol\n"
            "    set atom_idx $vmd_pick_atom\n"
            "    set sel [atomselect $mol \"index $atom_idx\"]\n"
            "    set aname [$sel get name]\n"
            "    set abeta [$sel get beta]\n"
            "    set axyz  [$sel get {x y z}]\n"
            "    $sel delete\n"
            "    if {[catch {\n"
            f"        set s [socket 127.0.0.1 {callback_port}]\n"
            "        fconfigure $s -buffering line -translation binary\n"
            "        puts $s \"PICK:$atom_idx:$aname:$abeta:$axyz\"\n"
            "        flush $s\n"
            "        close $s\n"
            "    } err]} {\n"
            "        # Python callback server not running, ignore\n"
            "    }\n"
            "}\n"
            f'puts "ESP_VMD_CALLBACK:{callback_port}"\n'
        )

    return f"""
# === Socket server: accept commands from Python GUI ===
set _esp_server_sock [socket -server _esp_accept -myaddr 127.0.0.1 {port}]
proc _esp_accept {{chan addr port}} {{
    fconfigure $chan -buffering line -translation binary
    fileevent $chan readable [list _esp_handle $chan]
}}
proc _esp_handle {{chan}} {{
    if [eof $chan] {{ close $chan; return }}
    gets $chan cmd
    if {{$cmd eq ""}} return
    if [catch {{uplevel #0 $cmd}} err] {{
        puts $chan "ERROR: $err"
    }} else {{
        puts $chan "OK"
    }}
    flush $chan
}}
puts "ESP_VMD_SERVER:{port}"

{pick_code}"""


def send_vmd_cmd(port, cmd, timeout=5):
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        sock.sendall((cmd + "\n").encode("utf-8"))
        time.sleep(0.3)
        resp = b""
        sock.settimeout(3)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
                if b"\n" in resp:
                    break
        except socket.timeout:
            pass
        return resp.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if sock:
            sock.close()


def render_current_view_tachyon(port, vmd_dir, tachyon_exe, output_path,
                                 resolution=(2000, 1500), log_func=None):
    def log(msg):
        if log_func:
            log_func(msg)
        print(msg)

    log(f"  渲染当前视角: port={port}, vmd_dir={vmd_dir}")

    # Clean up old files
    for fn in ["vmdscene.dat", "_esp_render.bmp"]:
        fp = os.path.join(vmd_dir, fn)
        if os.path.exists(fp):
            os.remove(fp)
            log(f"  已清理旧文件: {fn}")

    # Send render command to VMD
    log("  发送渲染命令到 VMD...")
    resp = send_vmd_cmd(port, "render Tachyon vmdscene.dat")
    if resp and "ERROR" in resp:
        log(f"  ✗ VMD 渲染失败: {resp}")
        return None

    # Wait for vmdscene.dat to be generated
    dat_path = os.path.join(vmd_dir, "vmdscene.dat")
    log(f"  等待 vmdscene.dat...")
    for i in range(30):
        if os.path.exists(dat_path) and os.path.getsize(dat_path) > 0:
            log(f"  ✓ vmdscene.dat 已生成 (大小: {os.path.getsize(dat_path)} bytes)")
            break
        time.sleep(0.5)
    else:
        log("  ✗ 等待 vmdscene.dat 超时")
        return None

    # Run Tachyon
    bmp_name = "_esp_render.bmp"
    args = [
        tachyon_exe, "vmdscene.dat",
        "-format", "BMP", "-o", bmp_name,
        "-res", str(resolution[0]), str(resolution[1]),
        "-numthreads", "4", "-aasamples", "24",
        "-fullshade",
    ]
    log(f"  运行 Tachyon: {tachyon_exe}")
    try:
        result = subprocess.run(args, capture_output=True, cwd=vmd_dir, timeout=600, text=True)
        if result.returncode != 0:
            log(f"  ✗ Tachyon 执行失败 (返回码: {result.returncode})")
            if result.stderr:
                log(f"    错误信息: {result.stderr[:500]}")
            return None
        log(f"  ✓ Tachyon 渲染完成")
    except subprocess.TimeoutExpired:
        log("  ✗ Tachyon 执行超时")
        return None
    except FileNotFoundError:
        log(f"  ✗ Tachyon 可执行文件不存在: {tachyon_exe}")
        return None

    # Check output
    bmp_path = os.path.join(vmd_dir, bmp_name)
    if not os.path.exists(bmp_path):
        log(f"  ✗ Tachyon 输出文件不存在: {bmp_path}")
        return None
    log(f"  ✓ BMP 文件已生成: {os.path.getsize(bmp_path)} bytes")

    # Convert to output format
    out = output_path
    if not out:
        out = os.path.join(vmd_dir, "esp_render.png")

    try:
        from PIL import Image
        img = Image.open(bmp_path)
        img.save(out)
        log(f"  ✓ 图片已保存: {out}")
    except ImportError:
        log("  ⚠ PIL 未安装，使用 BMP 格式")
        if not out.lower().endswith(".bmp"):
            out = os.path.splitext(out)[0] + ".bmp"
        shutil.copy(bmp_path, out)
    except Exception as e:
        log(f"  ✗ 图片保存失败: {e}")
        return None

    return out


# ── Worker Thread ─────────────────────────────────────────

class JobWorker(QThread):
    """Background worker for running Multiwfn + VMD pipeline."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, str)  # value, mode ('determinate'/'indeterminate')
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str, object)  # action, result data
    error_signal = pyqtSignal(str)

    def __init__(self, action, params, parent=None):
        super().__init__(parent)
        self.action = action
        self.params = params

    def log(self, msg):
        self.log_signal.emit(msg)

    def set_progress(self, value, mode='determinate'):
        self.progress_signal.emit(value, mode)

    def set_status(self, msg):
        self.status_signal.emit(msg)

    def run(self):
        try:
            if self.action == "preview":
                self._run_preview()
            elif self.action == "render_view":
                self._run_render_view()
            elif self.action == "area_chart":
                self._run_area_chart()
            elif self.action == "pick":
                self._run_pick()
        except Exception as e:
            self.error_signal.emit(str(e))

    def _run_pick(self):
        vmd_port = self.params.get("vmd_port")
        if not vmd_port:
            self.error_signal.emit("VMD 未启动")
            return
        resp = send_vmd_cmd(vmd_port, "mouse mode 4", timeout=3)
        if "ERROR" in resp:
            self.log(f"⚠ VMD 进入选取模式失败: {resp}")
        else:
            self.log("")
            self.log("━━━ 查询极值点 ━━━")
            self.log("VMD 已进入选取模式，请点击极值点球查询 ESP 值")
            self.log("（C = 极大值点, O = 极小值点）")
            self.set_status("选取模式：点击 VMD 中的极值点球（C/O 原子）")

    def _run_render_view(self):
        params = self.params
        vmd_port = params["vmd_port"]
        vmd_render_dir = params["vmd_render_dir"]
        tachyon_exe = params["tachyon_exe"]
        output_path = params["output_path"]
        resolution = params.get("resolution", (2000, 1500))

        self.log(f"\n正在渲染当前视角 ({resolution[0]}x{resolution[1]})...")
        t0 = time.time()
        result = render_current_view_tachyon(
            vmd_port, vmd_render_dir,
            tachyon_exe, output_path, resolution=resolution,
            log_func=self.log
        )
        dt = time.time() - t0
        if result:
            self.log(f"✓ 渲染完成 ({dt:.1f}s): {result}")
            self.finished_signal.emit("render_view", result)
        else:
            self.log(f"✗ 渲染失败 ({dt:.1f}s)")
            self.error_signal.emit("Tachyon 渲染失败，请检查日志")

    def _run_preview(self):
        params = self.params
        multiwfn_exe = params["multiwfn_exe"]
        vmd_exe = params["vmd_exe"]
        vmd_dir = params["vmd_dir"]
        mode = params["mode"]
        color_low = params["color_low"]
        color_high = params["color_high"]
        pt_size = params["pt_size"]
        input_files = params["input_files"]
        work_dir = params["work_dir"]

        self._run_pipeline(
            multiwfn_exe, vmd_exe, vmd_dir, mode,
            color_low, color_high, pt_size,
            input_files, work_dir
        )

    def _run_pipeline(self, multiwfn_exe, vmd_exe, vmd_dir, mode,
                      color_low, color_high, pt_size,
                      input_files, work_dir):

        self.set_busy(True)

        # Clean old files
        self._clean_vmd_dir(vmd_dir)

        nsystems = len(input_files)
        if mode == "all":
            steps_per_file = 2
        else:
            steps_per_file = 1
        total_steps = nsystems * steps_per_file
        completed_steps = 0

        self.set_status("开始计算...")
        self.set_progress(0)

        for idx, fch_path in enumerate(input_files):
            sys_num = idx + 1
            self.log(f"\n{'='*50}")
            self.log(f"处理文件 {sys_num}/{nsystems}: {os.path.basename(fch_path)}")
            self.log(f"模式: {mode}")

            tmp_dir = os.path.join(work_dir, f"sys{sys_num}")
            os.makedirs(tmp_dir, exist_ok=True)

            fch_name = f"sys{sys_num}.fch"
            fch_in_tmp = os.path.join(tmp_dir, fch_name)
            shutil.copy2(fch_path, fch_in_tmp)

            def make_progress_cb(file_idx, total_files, completed_val, total_val):
                def cb(line):
                    if not line.strip():
                        return
                    stripped = line.strip()
                    if len(stripped) < 3:
                        return
                    skip_patterns = ["========", "--------", "Multiwfn",
                                     "http://", "Version", "Cite"]
                    for sp in skip_patterns:
                        if sp in stripped:
                            return
                    pct_val, msg = _parse_progress(line)
                    if len(msg) > 80:
                        msg = msg[:77] + "..."
                    self.set_status(f"[文件 {file_idx}/{total_files}] {msg}")
                    if pct_val is not None:
                        step_base = (completed_val / total_val) * 100
                        step_range = (1 / total_val) * 100
                        overall = step_base + pct_val * step_range
                        self.set_progress(int(overall), mode='determinate')
                    else:
                        step_base = (completed_val / total_val) * 100
                        step_range = (1 / total_val) * 100
                        self.set_progress(int(step_base + step_range * 0.5), mode='determinate')
                return cb

            success = False
            if mode in ("pt", "all"):
                step_label = "ESPpt" if mode == "pt" else "ALL-PT"
                self.log(f"[1/{steps_per_file}] Multiwfn {step_label}...")
                self.set_status(f"计算 {step_label}: {os.path.basename(fch_path)}")
                self.set_progress(int((completed_steps / total_steps) * 100))

                cb = make_progress_cb(sys_num, nsystems, completed_steps, total_steps)
                ok, _ = run_multiwfn(multiwfn_exe, fch_in_tmp, CMD_ESPPT, tmp_dir,
                                     progress_cb=cb)
                if ok:
                    for fn in ("mol.pdb", "vtx.pdb"):
                        src = os.path.join(tmp_dir, fn)
                        if os.path.exists(src):
                            dest_fn = fn.replace(".pdb", f"{sys_num}.pdb")
                            shutil.copy2(src, os.path.join(vmd_dir, dest_fn))
                    self.log("  ESPpt OK")
                    success = True
                else:
                    self.log("  ESPpt FAILED")
                    continue
                completed_steps += 1

            if mode in ("iso", "all"):
                step_label = "ESPiso" if mode == "iso" else "ALL-ISO"
                self.log(f"[ISO] Multiwfn {step_label}...")
                self.set_status(f"计算 {step_label}: {os.path.basename(fch_path)}")
                self.set_progress(int((completed_steps / total_steps) * 100))

                cb = make_progress_cb(sys_num, nsystems, completed_steps, total_steps)
                ok, _ = run_multiwfn(multiwfn_exe, fch_in_tmp, CMD_ESPISO, tmp_dir,
                                     extra_args="-ESPrhoiso 0.001", progress_cb=cb)
                if ok:
                    src_density = os.path.join(tmp_dir, "density.cub")
                    src_totesp = os.path.join(tmp_dir, "totesp.cub")
                    iso_ok = True
                    if os.path.exists(src_density):
                        shutil.copy2(src_density, os.path.join(vmd_dir, f"density{sys_num}.cub"))
                    else:
                        self.log(f"  ERROR: density.cub not found in {tmp_dir}")
                        iso_ok = False
                    if os.path.exists(src_totesp):
                        shutil.copy2(src_totesp, os.path.join(vmd_dir, f"ESP{sys_num}.cub"))
                    else:
                        self.log(f"  ERROR: totesp.cub not found in {tmp_dir}")
                        iso_ok = False
                    if iso_ok:
                        self.log("  ESPiso OK")
                        success = True
                    else:
                        self.log("  ESPiso FAILED - missing output files")
                        continue
                else:
                    self.log("  ESPiso FAILED")
                    continue
                completed_steps += 1

            if mode in ("ext", "all"):
                step_label = "ESPext" if mode == "ext" else "ALL-EXT"
                self.log(f"[EXT] Multiwfn {step_label}...")
                self.set_status(f"计算 {step_label}: {os.path.basename(fch_path)}")
                self.set_progress(int((completed_steps / total_steps) * 100))

                cb = make_progress_cb(sys_num, nsystems, completed_steps, total_steps)
                ok, _ = run_multiwfn(multiwfn_exe, fch_in_tmp, CMD_ESPEXT, tmp_dir,
                                     progress_cb=cb)
                if ok:
                    for fn in ("mol.pdb", "vtx.pdb", "surfanalysis.pdb"):
                        src = os.path.join(tmp_dir, fn)
                        if os.path.exists(src):
                            shutil.copy2(src, os.path.join(vmd_dir, fn))
                    self.log("  ESPext OK")
                    success = True
                    sa_pdb = os.path.join(vmd_dir, "surfanalysis.pdb")
                    if os.path.exists(sa_pdb):
                        self._detect_esp_unit(sa_pdb)
                else:
                    self.log("  ESPext FAILED")
                    continue
                completed_steps += 1

        self.log(f"\n{'='*50}")
        self.log("生成 VMD 脚本...")
        self.set_status("生成 VMD 脚本...")
        self.set_progress(95)

        if mode == "pt":
            script = generate_vmd_script_pt(multiwfn_exe, vmd_dir, nsystems,
                                           color_low, color_high, pt_size)
        elif mode == "iso":
            script = generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high)
        elif mode == "ext":
            script = generate_vmd_script_ext(multiwfn_exe, vmd_dir)
        elif mode == "all":
            script = generate_vmd_script_all(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high)
            self.log("ALL 模式: 已生成组合脚本 (ISO 等值面 + EXT 极值点)")

        self.log(f"VMD 脚本: {script}")

        vmd_port = self.params.get("vmd_port")
        callback_port = self.params.get("callback_port")
        socket_code = socket_tcl_snippet(vmd_port, callback_port)
        with open(script, "a", encoding="utf-8") as f:
            f.write(socket_code)

        self.log(f"启动 VMD 预览 (交互模式, 端口 {vmd_port}, 回调端口 {callback_port})...")
        self.set_status("启动 VMD...")
        self.set_progress(100)
        self.finished_signal.emit("preview", {
            "vmd_port": vmd_port,
            "vmd_render_dir": vmd_dir,
            "vmd_exe": vmd_exe,
            "script": script,
            "mode": mode,
            "esp_unit": getattr(self, 'esp_unit', 'kcal/mol'),
        })

        self.set_busy(False)

    def _clean_vmd_dir(self, vmd_dir):
        if not os.path.isdir(vmd_dir):
            return
        patterns = [
            "density*.cub", "ESP*.cub", "mol*.pdb",
            "vtx*.pdb", "surfanalysis.pdb",
            "esp_auto_*.vmd", "_esp_render.tcl",
        ]
        cleaned = 0
        for pattern in patterns:
            for f in glob.glob(os.path.join(vmd_dir, pattern)):
                try:
                    os.remove(f)
                    cleaned += 1
                except Exception:
                    pass
        if cleaned > 0:
            self.log(f"已清理 {cleaned} 个旧文件")

    def _detect_esp_unit(self, pdb_path):
        try:
            with open(pdb_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("HEADER") or line.startswith("TITLE") or line.startswith("REMARK"):
                        if "eV" in line:
                            self.log(f"  单位检测: eV (来自 {os.path.basename(pdb_path)})")
                            return
                    if line.startswith("HETATM") or line.startswith("ATOM"):
                        break
            self.log("  单位检测: kcal/mol (默认)")
        except Exception:
            pass

    def _run_area_chart(self):
        params = self.params
        multiwfn_exe = params["multiwfn_exe"]
        input_files = params["input_files"]
        work_dir = params["work_dir"]
        range_low = params["range_low"]
        range_high = params["range_high"]
        n_bins = params["n_bins"]
        out_dir = params.get("out_dir", os.path.dirname(input_files[0]))

        self.set_busy(True)
        self.set_status("正在计算 ESP 分面面积分布...")
        self.log("")
        self.log("━━━ ESP 分区面积分布分析 ━━━")
        self.log(f"范围: {range_low} ~ {range_high} kcal/mol, 分区数: {n_bins}")

        all_data = []
        n_total = len(input_files)
        for idx, fch_path in enumerate(input_files):
            sys_num = idx + 1
            fch_name = os.path.basename(fch_path)
            self.log(f"\n分析文件 {sys_num}/{n_total}: {fch_name}")

            tmp_dir = os.path.join(work_dir, f"area_sys{sys_num}")
            os.makedirs(tmp_dir, exist_ok=True)
            fch_tmp = os.path.join(tmp_dir, f"sys{sys_num}.fch")
            shutil.copy2(fch_path, fch_tmp)

            def area_cb(line):
                stripped = line.rstrip()
                if len(stripped) > 3:
                    if len(stripped) > 70:
                        stripped = stripped[:67] + "..."
                    self.set_status(f"[面积分析 {sys_num}/{n_total}] {stripped}")
                pct, _ = _parse_progress(line)
                if pct is not None:
                    self.set_progress(int(((idx + pct) / n_total) * 100))

            ok, data = run_multiwfn_area_dist(
                multiwfn_exe, fch_tmp, tmp_dir,
                range_low=range_low, range_high=range_high,
                n_bins=n_bins, unit_code=3,
                progress_cb=area_cb,
                log_cb=self.log,
            )

            if data:
                all_data.append((fch_name, data))
                n_found = len(data)
                total_area = sum(d[1] for d in data)
                self.log(f"  已获取 {n_found} 个分区数据, 总表面积 = {total_area:.2f} \u00c5\u00b2")
            else:
                if not ok:
                    self.log("  Multiwfn 返回异常且未解析到面积分布数据")
                else:
                    self.log("  Multiwfn 正常完成但未解析到面积分布数据")

        if not all_data:
            self.log("\n✗ 未获取到有效面积分布数据")
            self.set_busy(False)
            self.set_status("✗ 分析失败")
            return

        results = []
        for fname, data in all_data:
            base = os.path.splitext(fname)[0]
            chart_path = os.path.join(out_dir, f"{base}_esp_area.png")
            self.log(f"\n生成柱形图: {os.path.basename(chart_path)}")
            result_path = plot_esp_area_histogram(
                data, chart_path,
                title=f"ESP Area Distribution - {fname}",
                xlabel="ESP (kcal/mol)",
            )
            if result_path:
                self.log(f"✓ 图表已保存: {result_path}")
                results.append(result_path)
            else:
                self.log("✗ 图表生成失败")

            data_path = os.path.join(out_dir, f"{base}_esp_area_data.txt")
            saved = save_area_data_file(data, data_path)
            if saved:
                self.log(f"✓ 数据文件已保存: {saved}")
                results.append(saved)

        self.set_progress(100)
        self.set_status("✓ ESP 分区面积图完成")
        self.set_busy(False)
        self.finished_signal.emit("area_chart", results)

    def set_busy(self, busy):
        pass  # handled by main thread via signals


class BinSettingsDialog(QDialog):
    """Dialog for ESP area distribution bin settings."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ESP 分区设置")
        self.setFixedSize(340, 260)
        self.setStyleSheet("background-color: #f0f0f0;")

        layout = QGridLayout(self)

        layout.addWidget(QLabel("ESP 范围下限:"), 0, 0)
        self.edit_rlo = QLineEdit("-25")
        self.edit_rlo.setFixedWidth(80)
        layout.addWidget(self.edit_rlo, 0, 1)

        layout.addWidget(QLabel("ESP 范围上限:"), 1, 0)
        self.edit_rhi = QLineEdit("22")
        self.edit_rhi.setFixedWidth(80)
        layout.addWidget(self.edit_rhi, 1, 1)

        layout.addWidget(QLabel("分区数量:"), 2, 0)
        self.edit_bins = QLineEdit("15")
        self.edit_bins.setFixedWidth(80)
        layout.addWidget(self.edit_bins, 2, 1)

        note = QLabel("(默认 kcal/mol 单位)")
        note.setStyleSheet("color: #888; font-size: 8pt;")
        layout.addWidget(note, 3, 1)

        btn_layout = QHBoxLayout()
        btn_start = QPushButton("开始分析")
        btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 20px;"
        )
        btn_start.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("background-color: #ddd; padding: 6px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_start)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout, 4, 0, 1, 2)

    def get_values(self):
        try:
            rlo = float(self.edit_rlo.text())
            rhi = float(self.edit_rhi.text())
            bins = int(self.edit_bins.text())
            return rlo, rhi, bins
        except ValueError:
            return None


# ── Main GUI ───────────────────────────────────────────────

class ESPGuiQt(QMainWindow):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    pick_signal = pyqtSignal(str)
    pick_mode_signal = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP Surface Visualization v1.0 (PyQt5)")
        self.setMinimumSize(800, 700)

        self.config = load_config()
        self.input_files = []
        self.work_dir = tempfile.mkdtemp(prefix="esp_gui_qt_")
        self.vmd_process = None
        self.vmd_port = None
        self.vmd_render_dir = None
        self.callback_port = None
        self.callback_server = None
        self.callback_thread = None
        self.esp_unit = "kcal/mol"
        self._worker = None
        self._vmd_persist_sock = None

        self._build_ui()
        self.log_signal.connect(self.log)
        self.status_signal.connect(self.set_status)
        self.pick_signal.connect(self._handle_pick)
        self.pick_mode_signal.connect(self._set_pick_mode_ui)
        self._apply_style()
        self.log("ESP Surface Visualization GUI v1.0 (PyQt5) 已启动")
        self.log(f"工作目录: {self.work_dir}")

    def closeEvent(self, event):
        self._stop_callback_server()
        self._close_persist_sock()
        if self.vmd_process:
            try:
                self.vmd_process.terminate()
                self.vmd_process.wait(timeout=5)
            except Exception:
                pass
        self._cleanup_work_dir()
        event.accept()

    def _cleanup_work_dir(self):
        if os.path.exists(self.work_dir):
            try:
                shutil.rmtree(self.work_dir)
                self.log(f"已清理临时目录: {self.work_dir}")
            except Exception as e:
                self.log(f"清理临时目录失败: {e}")

    def _apply_style(self):
        self.setStyleSheet("""
/* ── Global ── */
QMainWindow {
    background-color: #E4EAF2;
}

QWidget {
    font-family: "Microsoft YaHei", "Segoe UI", "Consolas", sans-serif;
    font-size: 9.5pt;
    color: #2C3E50;
}

/* ── Title ── */
QLabel#TitleLabel {
    color: #0D47A1;
    font-size: 16pt;
    font-weight: bold;
}

QLabel#SubTitleLabel {
    color: #5C6BC0;
    font-size: 8.5pt;
    padding: 0px 8px 8px 8px;
}

/* ── Group Box ── */
QGroupBox {
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    margin-top: 16px;
    padding: 18px 12px 12px 12px;
    background-color: #FFFFFF;
    font-weight: bold;
    font-size: 10pt;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 2px 12px 2px 12px;
    color: #FFFFFF;
    background-color: #1565C0;
    border-radius: 4px;
    font-size: 9pt;
}

/* ── Labels ── */
QLabel {
    color: #4A5568;
    padding: 1px 0px;
}

/* ── Line Edit ── */
QLineEdit {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 10px;
    color: #2C3E50;
}

QLineEdit:focus {
    border: 1px solid #1E88E5;
    background-color: #F8FAFE;
}

/* ── Combo Box ── */
QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 10px;
    color: #2C3E50;
}

QComboBox:focus {
    border: 1px solid #1E88E5;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    selection-background-color: #E3F2FD;
    selection-color: #1565C0;
}

/* ── Radio Button ── */
QRadioButton {
    color: #4A5568;
    spacing: 6px;
    padding: 3px 6px;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 2px solid #A0AEC0;
    background-color: #FFFFFF;
}

QRadioButton::indicator:checked {
    border: 2px solid #1E88E5;
    background-color: #1E88E5;
}

QRadioButton::indicator:hover {
    border: 2px solid #5C6BC0;
}

QRadioButton:checked {
    color: #1565C0;
    font-weight: bold;
}

/* ── Check Box ── */
QCheckBox {
    color: #4A5568;
    spacing: 6px;
    padding: 3px 6px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 3px;
    border: 2px solid #A0AEC0;
    background-color: #FFFFFF;
}

QCheckBox::indicator:checked {
    border: 2px solid #1E88E5;
    background-color: #1E88E5;
}

QCheckBox::indicator:hover {
    border: 2px solid #5C6BC0;
}

QCheckBox:checked {
    color: #1565C0;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    border: 1px solid #CBD5E1;
    height: 8px;
    background-color: #F1F5F9;
    border-radius: 4px;
}

QSlider::sub-page:horizontal {
    background-color: #1E88E5;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background-color: #FFFFFF;
    border: 2px solid #1E88E5;
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background-color: #E3F2FD;
    border: 2px solid #1565C0;
}

QSlider::handle:horizontal:pressed {
    background-color: #BBDEFB;
    border: 2px solid #0D47A1;
}

QSlider:disabled {
    color: #94A3B8;
}

QSlider::groove:horizontal:disabled {
    background-color: #F1F5F9;
    border: 1px solid #E2E8F0;
}

QSlider::handle:horizontal:disabled {
    background-color: #F1F5F9;
    border: 2px solid #E2E8F0;
}

/* ── Push Button ── */
QPushButton {
    background-color: #F8FAFE;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 6px 16px;
    color: #2C3E50;
    font-weight: bold;
    font-size: 9pt;
}

QPushButton:hover {
    background-color: #E3F2FD;
    border: 1px solid #1E88E5;
    color: #1565C0;
}

QPushButton:pressed {
    background-color: #BBDEFB;
    border: 1px solid #1565C0;
}

QPushButton:disabled {
    background-color: #F1F5F9;
    border: 1px solid #E2E8F0;
    color: #94A3B8;
}

/* ── List Widget ── */
QListWidget {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    color: #2C3E50;
    font-family: Consolas;
    font-size: 9pt;
}

QListWidget::item {
    padding: 4px 8px;
}

QListWidget::item:selected {
    background-color: #E3F2FD;
    color: #1565C0;
}

/* ── Plain Text Edit ── */
QPlainTextEdit {
    background-color: #F8FAFE;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    color: #2C3E50;
    font-family: Consolas;
    font-size: 9pt;
}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(10, 8, 10, 8)

        # ── Title ──
        title_lbl = QLabel("\u25c8  静电势表面可视化  \u25c8")
        title_lbl.setObjectName("TitleLabel")
        title_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_lbl)

        sub_lbl = QLabel("Multiwfn + VMD + Tachyon  |  ESP Surface Visualization v1.0 PyQt5")
        sub_lbl.setObjectName("SubTitleLabel")
        sub_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(sub_lbl)

        # ── Tab Widget ──
        tab_widget = QTabWidget()

        # ── Tab 1: 输入与设置 ──
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        tab1_layout.setSpacing(6)
        tab1_layout.setContentsMargins(6, 6, 6, 6)

        # ── Input Files ──
        in_grp = QGroupBox("输入文件 (.fch / .fchk)")
        in_layout = QVBoxLayout(in_grp)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加文件")
        btn_add.setStyleSheet("background-color: #1565C0; border: 1px solid #0D47A1; color: white; font-weight: bold; padding: 6px 16px; border-radius: 5px;")
        btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(btn_add)

        btn_adddir = QPushButton("添加目录")
        btn_adddir.setStyleSheet("background-color: #1565C0; border: 1px solid #0D47A1; color: white; font-weight: bold; padding: 6px 16px; border-radius: 5px;")
        btn_adddir.clicked.connect(self._add_dir)
        btn_row.addWidget(btn_adddir)

        btn_clear = QPushButton("清空列表")
        btn_clear.setStyleSheet("background-color: #E53935; border: 1px solid #C62828; color: white; font-weight: bold; padding: 6px 16px; border-radius: 5px;")
        btn_clear.clicked.connect(self._clear_files)
        btn_row.addWidget(btn_clear)

        self.lbl_filecount = QLabel("(0 个文件)")
        self.lbl_filecount.setStyleSheet("color: #666;")
        btn_row.addWidget(self.lbl_filecount)
        btn_row.addStretch()
        in_layout.addLayout(btn_row)

        self.lst_files = QListWidget()
        self.lst_files.setMaximumHeight(100)
        in_layout.addWidget(self.lst_files)

        tab1_layout.addWidget(in_grp)

        # ── Mode & Settings ──
        set_grp = QGroupBox("分析模式与设置")
        set_layout = QGridLayout(set_grp)

        set_layout.addWidget(QLabel("模式:"), 0, 0)

        self.btn_group_mode = QButtonGroup(self)
        modes = [
            ("PT (顶点着色)", "pt"),
            ("ISO (等值面着色)", "iso"),
            ("EXT (极值点)", "ext"),
            ("ALL (全部叠加)", "all"),
        ]
        self.radio_mode = {}
        for i, (txt, val) in enumerate(modes):
            rb = QRadioButton(txt)
            self.btn_group_mode.addButton(rb, i)
            rb.setProperty("mode_value", val)
            if val == "pt":
                rb.setChecked(True)
            rb.toggled.connect(self._on_mode_change)
            self.radio_mode[val] = rb
            set_layout.addWidget(rb, 0, 1 + i)

        set_layout.addWidget(QLabel("色彩下限:"), 1, 0)
        self.edit_clow = QLineEdit("-50")
        self.edit_clow.setMaximumWidth(80)
        set_layout.addWidget(self.edit_clow, 1, 1)

        set_layout.addWidget(QLabel("色彩上限:"), 1, 2)
        self.edit_chigh = QLineEdit("50")
        self.edit_chigh.setMaximumWidth(80)
        set_layout.addWidget(self.edit_chigh, 1, 3)

        tip = QLabel("  (PT模式单位kcal/mol, ISO模式单位a.u.)")
        tip.setStyleSheet("color: #888; font-size: 8pt;")
        set_layout.addWidget(tip, 1, 4, 1, 2)

        set_layout.addWidget(QLabel("点大小(PT):"), 2, 0)
        self.edit_ptsize = QLineEdit("2.0")
        self.edit_ptsize.setMaximumWidth(80)
        set_layout.addWidget(self.edit_ptsize, 2, 1)

        set_layout.addWidget(QLabel("渲染分辨率:"), 2, 2)
        self.combo_res = QComboBox()
        self.combo_res.addItems(["2000x1500", "1200x900", "3000x2250", "4000x3000"])
        self.combo_res.setMinimumWidth(120)
        set_layout.addWidget(self.combo_res, 2, 3)

        set_layout.addWidget(QLabel("透明度:"), 3, 0)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(70)
        self.opacity_slider.setEnabled(False)
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        set_layout.addWidget(self.opacity_slider, 3, 1, 1, 3)
        self.opacity_value_label = QLabel("0.70")
        self.opacity_value_label.setMinimumWidth(40)
        self.opacity_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        set_layout.addWidget(self.opacity_value_label, 3, 4)

        set_layout.addWidget(QLabel("输出路径:"), 4, 0)
        self.edit_output = QLineEdit()
        set_layout.addWidget(self.edit_output, 4, 1, 1, 3)
        btn_output = QPushButton("浏览")
        btn_output.clicked.connect(self._browse_output)
        set_layout.addWidget(btn_output, 4, 4)

        tab1_layout.addWidget(set_grp)
        tab1_layout.addStretch()
        tab_widget.addTab(tab1, "输入与设置")

        # ── Tab 2: 路径设置 ──
        tab2 = QWidget()
        tab2_layout = QVBoxLayout(tab2)
        tab2_layout.setSpacing(6)
        tab2_layout.setContentsMargins(6, 6, 6, 6)

        path_grp = QGroupBox("路径设置")
        path_layout = QGridLayout(path_grp)

        path_layout.addWidget(QLabel("Multiwfn:"), 0, 0)
        self.edit_mwfn = QLineEdit(self.config["multiwfn"])
        path_layout.addWidget(self.edit_mwfn, 0, 1)
        btn_mwfn = QPushButton("浏览")
        btn_mwfn.clicked.connect(self._browse_mwfn)
        path_layout.addWidget(btn_mwfn, 0, 2)

        path_layout.addWidget(QLabel("VMD exe:"), 1, 0)
        self.edit_vmd = QLineEdit(self.config["vmd"])
        path_layout.addWidget(self.edit_vmd, 1, 1)
        btn_vmd = QPushButton("浏览")
        btn_vmd.clicked.connect(self._browse_vmd)
        path_layout.addWidget(btn_vmd, 1, 2)

        path_layout.addWidget(QLabel("VMD 工作目录:"), 2, 0)
        self.edit_vmdir = QLineEdit(
            self.config.get("vmd_dir", os.path.dirname(self.config["vmd"])) or ""
        )
        path_layout.addWidget(self.edit_vmdir, 2, 1)
        btn_vmdir = QPushButton("浏览")
        btn_vmdir.clicked.connect(self._browse_vmdir)
        path_layout.addWidget(btn_vmdir, 2, 2)

        path_layout.addWidget(QLabel("Tachyon:"), 3, 0)
        self.edit_tachyon = QLineEdit(self.config.get("tachyon", DEFAULT_TACHYON))
        path_layout.addWidget(self.edit_tachyon, 3, 1)
        btn_tachyon = QPushButton("浏览")
        btn_tachyon.clicked.connect(self._browse_tachyon)
        path_layout.addWidget(btn_tachyon, 3, 2)

        btn_save = QPushButton("保存路径配置")
        btn_save.setStyleSheet(
            "background-color: #1565C0; border: 1px solid #0D47A1; color: white; font-weight: bold; padding: 6px 16px; border-radius: 5px;"
        )
        btn_save.clicked.connect(self._save_paths)
        path_layout.addWidget(btn_save, 0, 3, 4, 1)

        tab2_layout.addWidget(path_grp)
        tab2_layout.addStretch()
        tab_widget.addTab(tab2, "路径设置")

        main_layout.addWidget(tab_widget)

        # ── Action Buttons ──
        act_layout = QHBoxLayout()

        self.btn_preview = QPushButton("▶ VMD 预览")
        self.btn_preview.setFixedHeight(42)
        self.btn_preview.setStyleSheet(
            "background-color: #1565C0; border: 1px solid #0D47A1; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
        )
        self.btn_preview.clicked.connect(self._action_preview)
        act_layout.addWidget(self.btn_preview)

        self.btn_render_view = QPushButton("📷 渲染当前视角")
        self.btn_render_view.setEnabled(False)
        self.btn_render_view.setFixedHeight(42)
        self.btn_render_view.setStyleSheet(
            "background-color: #FB8C00; border: 1px solid #EF6C00; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
        )
        self.btn_render_view.clicked.connect(self._action_render_view)
        act_layout.addWidget(self.btn_render_view)

        self.btn_pick = QPushButton("🔍 查询极值点")
        self.btn_pick.setEnabled(False)
        self.btn_pick.setFixedHeight(42)
        self.btn_pick.setStyleSheet(
            "background-color: #7E57C2; border: 1px solid #5E35B1; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
        )
        self.btn_pick.clicked.connect(self._action_pick)
        act_layout.addWidget(self.btn_pick)

        self.btn_area_chart = QPushButton("📊 ESP分区面积图")
        self.btn_area_chart.setFixedHeight(42)
        self.btn_area_chart.setStyleSheet(
            "background-color: #6D4C41; border: 1px solid #5D4037; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
        )
        self.btn_area_chart.clicked.connect(self._action_area_chart)
        act_layout.addWidget(self.btn_area_chart)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setFixedHeight(42)
        self.btn_stop.setStyleSheet(
            "background-color: #E53935; border: 1px solid #C62828; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
        )
        self.btn_stop.clicked.connect(self._action_stop)
        act_layout.addWidget(self.btn_stop)

        act_layout.addStretch()
        main_layout.addLayout(act_layout)

        # ── Progress Bar ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #4A5568; font-family: Consolas; font-size: 9pt;")
        main_layout.addWidget(self.lbl_status)

        # ── Bottom Panel: Log (left) + Pick Table (right) ──
        bottom_splitter = QSplitter(Qt.Horizontal)

        log_grp = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_grp)
        log_layout.setContentsMargins(4, 4, 4, 4)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(
            "background-color: #F8FAFE; color: #2C3E50; border: 1px solid #CBD5E1; border-radius: 5px; "
            "font-family: Consolas; font-size: 9pt;"
        )
        log_layout.addWidget(self.txt_log)
        bottom_splitter.addWidget(log_grp)

        pick_grp = QGroupBox("极值点查询结果")
        pick_layout = QVBoxLayout(pick_grp)
        pick_layout.setContentsMargins(4, 4, 4, 4)

        hdr_row = QHBoxLayout()
        self.lbl_pick_count = QLabel("(0 个极值点)")
        self.lbl_pick_count.setStyleSheet("color: #666; font-size: 9pt;")
        hdr_row.addWidget(self.lbl_pick_count)
        hdr_row.addStretch()
        btn_clear_table = QPushButton("清空表格")
        btn_clear_table.setStyleSheet(
            "background-color: #E53935; border: 1px solid #C62828; color: white; "
            "font-weight: bold; padding: 4px 14px; border-radius: 4px; font-size: 9pt;"
        )
        btn_clear_table.clicked.connect(self._clear_pick_table)
        hdr_row.addWidget(btn_clear_table)
        pick_layout.addLayout(hdr_row)

        self.pick_table = QTableWidget()
        self.pick_table.setColumnCount(3)
        self.pick_table.setHorizontalHeaderLabels(["#", "类型", "ESP 值"])
        self.pick_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.pick_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.pick_table.setColumnWidth(0, 32)
        self.pick_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pick_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pick_table.verticalHeader().setVisible(False)
        self.pick_table.setStyleSheet(
            "background-color: #FFFFFF; color: #2C3E50; border: 1px solid #CBD5E1; "
            "border-radius: 4px; font-family: Consolas; font-size: 9pt; "
            "gridline-color: #E8ECF1;"
        )
        pick_layout.addWidget(self.pick_table)
        bottom_splitter.addWidget(pick_grp)

        bottom_splitter.setStretchFactor(0, 3)
        bottom_splitter.setStretchFactor(1, 1)
        bottom_splitter.setSizes([600, 250])
        main_layout.addWidget(bottom_splitter, 1)

    # ── Logging ──
    def log(self, msg):
        self.txt_log.appendPlainText(msg)

    def set_progress(self, value, mode='determinate'):
        if mode == 'indeterminate':
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, int(value))))

    def set_status(self, msg):
        self.lbl_status.setText(msg)

    # ── VMD Pick Callback ──
    def _start_callback_server(self):
        self._stop_callback_server()
        self.callback_port = find_free_port()

        def server_loop():
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("127.0.0.1", self.callback_port))
                srv.listen(1)
                srv.settimeout(1.0)
                self.callback_server = srv
                self.log_signal.emit(f"[回调] 服务器已启动，监听端口 {self.callback_port}")
                while self.callback_server is srv:
                    try:
                        conn, addr = srv.accept()
                        conn.settimeout(3.0)
                        data = b""
                        try:
                            while True:
                                chunk = conn.recv(4096)
                                if not chunk:
                                    break
                                data += chunk
                                if b"\n" in data:
                                    break
                        except socket.timeout:
                            pass
                        conn.close()
                        if data:
                            msg = data.decode("utf-8", errors="replace").strip()
                            if msg.startswith("PICK:"):
                                self.log_signal.emit(f"[回调] 收到 VMD 点击数据: {msg}")
                                self.pick_signal.emit(msg)
                            else:
                                self.log_signal.emit(f"[回调] 收到未知数据: {msg[:80]}")
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    except Exception as e:
                        self.log_signal.emit(f"[回调] 错误: {e}")
                        break
                self.log_signal.emit(f"[回调] 服务器已停止 (端口 {self.callback_port})")
            except OSError as e:
                self.log_signal.emit(f"[回调] 启动失败: {e}")

        t = threading.Thread(target=server_loop, daemon=True)
        t.start()
        self.callback_thread = t

    def _stop_callback_server(self):
        srv = self.callback_server
        self.callback_server = None
        if srv:
            try:
                srv.close()
            except Exception:
                pass
        self.callback_port = None

    def _handle_pick(self, msg):
        try:
            parts = msg.split(":", 4)
            atom_idx = parts[1]
            atom_name = parts[2]
            beta_str = parts[3]
            xyz_str = parts[4].strip("{} ")

            try:
                beta_val = float(beta_str)
            except ValueError:
                beta_val = None

            xyz_parts = xyz_str.split()
            if len(xyz_parts) >= 3:
                x, y, z = (
                    f"{float(xyz_parts[0]):.3f}",
                    f"{float(xyz_parts[1]):.3f}",
                    f"{float(xyz_parts[2]):.3f}"
                )
            else:
                x, y, z = "?", "?", "?"

            if atom_name == "C":
                ext_type = "极大值点"
            elif atom_name == "O":
                ext_type = "极小值点"
            else:
                ext_type = f"原子({atom_name})"

            pdb_line = int(atom_idx) + 1
            unit = self.esp_unit

            if beta_val is not None:
                esp_str = f"{beta_val:.2f} {unit}"
                self.log(
                    f"  📌 极值点 #{pdb_line} (index {atom_idx}): "
                    f"ESP = {esp_str} ({ext_type})"
                )
                self.log(f"     坐标: ({x}, {y}, {z})")
                self.set_status(
                    f"ESP = {esp_str} ({ext_type}, index {atom_idx})"
                )
            else:
                esp_str = "N/A"
                self.log(f"  📌 极值点 #{pdb_line} (index {atom_idx}): {ext_type}")
                self.log(f"     坐标: ({x}, {y}, {z})")

            row = self.pick_table.rowCount()
            self.pick_table.insertRow(row)
            items = [str(row + 1), ext_type, esp_str]
            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 1 and ext_type == "极大值点":
                    item.setForeground(QColor("#E65100"))
                elif col == 1 and ext_type == "极小值点":
                    item.setForeground(QColor("#0D47A1"))
                self.pick_table.setItem(row, col, item)

            self.lbl_pick_count.setText(f"({self.pick_table.rowCount()} 个极值点)")

        except (IndexError, ValueError) as e:
            self.log(f"  ⚠ 解析 VMD pick 数据失败: {msg} ({e})")

    def _clear_pick_table(self):
        self.pick_table.setRowCount(0)
        self.lbl_pick_count.setText("(0 个极值点)")
        self.log("已清空极值点查询表格")

    def _set_pick_mode_ui(self, active):
        if active:
            self.btn_pick.setText("● 选取中...")
            self.btn_pick.setStyleSheet(
                "background-color: #E65100; border: 1px solid #BF360C; color: white; font-weight: bold; "
                "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
            )
        else:
            self.btn_pick.setText("🔍 查询极值点")
            self.btn_pick.setStyleSheet(
                "background-color: #7E57C2; border: 1px solid #5E35B1; color: white; font-weight: bold; "
                "font-size: 11pt; padding: 6px 22px; border-radius: 5px;"
            )

    def _on_opacity_slider_changed(self, val):
        op = val / 100.0
        self.opacity_value_label.setText(f"{op:.2f}")
        self._send_vmd_cmd_fast(f"material change opacity EdgyGlass {op}")

    def _send_vmd_cmd_fast(self, cmd):
        if not self.vmd_port:
            return
        try:
            if self._vmd_persist_sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(("127.0.0.1", self.vmd_port))
                self._vmd_persist_sock = sock
            self._vmd_persist_sock.sendall((cmd + "\n").encode("utf-8"))
            resp = b""
            try:
                self._vmd_persist_sock.settimeout(0.5)
                while True:
                    chunk = self._vmd_persist_sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if b"\n" in resp:
                        break
            except socket.timeout:
                pass
            self._vmd_persist_sock.settimeout(5)
        except Exception:
            self._close_persist_sock()

    def _close_persist_sock(self):
        if self._vmd_persist_sock:
            try:
                self._vmd_persist_sock.close()
            except Exception:
                pass
            self._vmd_persist_sock = None

    def _set_main_buttons_enabled(self, enabled):
        for btn in [self.btn_preview, self.btn_area_chart]:
            btn.setEnabled(enabled)
        if enabled:
            self.btn_render_view.setEnabled(False)
            self.btn_pick.setEnabled(False)
        else:
            self.opacity_slider.setEnabled(False)

    # ── Mode change ──
    def _on_mode_change(self):
        for rb in self.btn_group_mode.buttons():
            if rb.isChecked():
                mode = rb.property("mode_value")
                break
        else:
            mode = "pt"

        if mode == "pt":
            self.edit_clow.setText("-50")
            self.edit_chigh.setText("50")
        elif mode == "iso":
            self.edit_clow.setText("-0.03")
            self.edit_chigh.setText("0.03")
        elif mode == "ext":
            self.edit_clow.setText("-50")
            self.edit_chigh.setText("50")
        elif mode == "all":
            self.edit_clow.setText("-0.03")
            self.edit_chigh.setText("0.03")

    def _get_current_mode(self):
        for rb in self.btn_group_mode.buttons():
            if rb.isChecked():
                return rb.property("mode_value")
        return "pt"

    # ── Browse callbacks ──
    def _browse_mwfn(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 Multiwfn.exe", "", "EXE (*.exe)")
        if p:
            self.edit_mwfn.setText(p)

    def _browse_vmd(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 vmd.exe", "", "EXE (*.exe)")
        if p:
            self.edit_vmd.setText(p)
            if not self.edit_vmdir.text():
                self.edit_vmdir.setText(os.path.dirname(p))

    def _browse_vmdir(self):
        p = QFileDialog.getExistingDirectory(self, "选择 VMD 工作目录")
        if p:
            self.edit_vmdir.setText(p)

    def _browse_tachyon(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择 tachyon_WIN32.exe", "", "EXE (*.exe)"
        )
        if p:
            self.edit_tachyon.setText(p)

    def _browse_output(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "保存渲染输出", "", "PNG (*.png);;BMP (*.bmp)"
        )
        if p:
            self.edit_output.setText(p)

    def _save_paths(self):
        save_config(
            self.edit_mwfn.text(),
            self.edit_vmd.text(),
            self.edit_vmdir.text(),
            self.edit_tachyon.text(),
        )
        self.log("路径配置已保存")

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 .fch / .fchk 文件", "",
            "FCH/FCHK (*.fch *.fchk);;All (*.*)"
        )
        for f in files:
            if f not in self.input_files:
                self.input_files.append(f)
                self.lst_files.addItem(f)
        self.lbl_filecount.setText(f"({len(self.input_files)} 个文件)")

    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 .fch 文件的目录")
        if not d:
            return
        for ext in ("*.fch", "*.fchk"):
            for f in glob.glob(os.path.join(d, ext)):
                if f not in self.input_files:
                    self.input_files.append(f)
                    self.lst_files.addItem(f)
        self.lbl_filecount.setText(f"({len(self.input_files)} 个文件)")

    def _clear_files(self):
        self.input_files.clear()
        self.lst_files.clear()
        self.lbl_filecount.setText("(0 个文件)")

    def _action_preview(self):
        self._start_job("preview")

    def _action_render_view(self):
        if not self.vmd_port:
            QMessageBox.warning(self, "提示", "请先点击 [▶ VMD 预览] 启动 VMD")
            return

        tachyon_exe = self.edit_tachyon.text().strip()
        if not os.path.isfile(tachyon_exe):
            QMessageBox.critical(self, "错误", f"Tachyon 不存在:\n{tachyon_exe}")
            return

        try:
            res_str = self.combo_res.currentText().strip()
            w, h = res_str.split("x")
            resolution = (int(w), int(h))
        except (ValueError, AttributeError):
            resolution = (2000, 1500)

        output_path = self.edit_output.text().strip()
        if not output_path:
            if self.input_files:
                output_path = os.path.splitext(self.input_files[0])[0] + "_esp.png"
            else:
                output_path = os.path.join(
                    self.vmd_render_dir or ".", "esp_render.png"
                )

        self._start_render_view_job(tachyon_exe, output_path, resolution)

    def _action_pick(self):
        """Switch VMD to pick mode for querying extrema ESP values.

        Sends mouse mode 4 via background thread to avoid blocking the Qt
        event loop. The callback server runs in its own thread.
        """
        if not self.vmd_port:
            QMessageBox.warning(self, "提示", "请先点击 [▶ VMD 预览] 启动 VMD")
            return

        self.log("")
        self.log("━━━ 查询极值点 ━━━")
        self.log(f"VMD 端口: {self.vmd_port}, 回调端口: {self.callback_port}")

        if not self.callback_server:
            self.log("✗ 回调服务器未运行！请重新点击 [▶ VMD 预览] 启动预览")
            self.set_status("选取失败：回调服务器未运行")
            QMessageBox.warning(self, "提示",
                "回调服务器未运行，无法接收 VMD 点击事件。\n\n"
                "请重新点击 [▶ VMD 预览] 启动 VMD 预览后再试。")
            return

        self.log("发送选取模式命令到 VMD...")

        self._set_pick_mode_ui(True)

        def pick_worker():
            resp = send_vmd_cmd(self.vmd_port, "mouse mode 4", timeout=3)
            if "ERROR" in resp:
                self.log_signal.emit(f"✗ VMD 进入选取模式失败: {resp}")
                self.log_signal.emit("提示：请确保 VMD 窗口仍在运行，且已完成预览加载")
                self.status_signal.emit("选取模式失败")
                self.pick_mode_signal.emit(False)
            else:
                self.log_signal.emit("✓ VMD 已进入选取模式 (mouse mode 4)")
                self.log_signal.emit("请在 VMD 窗口中点击极值点球查询 ESP 值")
                self.log_signal.emit("（黄色球 = 静电势极大值点，青色球 = 静电势极小值点）")
                self.log_signal.emit("提示：先点击 VMD 图形窗口激活它，再点击极值点球")
                self.status_signal.emit("选取模式：点击 VMD 中的极值点球（C/O 原子）")

        threading.Thread(target=pick_worker, daemon=True).start()

    def _action_stop(self):
        self._close_persist_sock()
        os.system("taskkill /F /IM vmd.exe 2>nul")
        self.log("已发送停止信号")
        self._set_pick_mode_ui(False)
        self.opacity_slider.setEnabled(False)

    def _action_area_chart(self):
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请先添加输入文件")
            return

        multiwfn_exe = self.edit_mwfn.text()
        if not os.path.isfile(multiwfn_exe):
            QMessageBox.critical(self, "错误", f"Multiwfn 不存在:\n{multiwfn_exe}")
            return

        if not _HAS_MPL:
            QMessageBox.critical(
                self, "缺少依赖",
                "需要 matplotlib 和 numpy 来绘制柱形图。\n"
                "请运行: pip install matplotlib numpy"
            )
            return

        dlg = BinSettingsDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return

        vals = dlg.get_values()
        if vals is None:
            QMessageBox.critical(self, "错误", "请输入有效的数值")
            return

        range_low, range_high, n_bins = vals
        if n_bins < 2 or n_bins > 50:
            QMessageBox.critical(self, "错误", "分区数量应在 2~50 之间")
            return

        params = {
            "multiwfn_exe": multiwfn_exe,
            "input_files": list(self.input_files),
            "work_dir": self.work_dir,
            "range_low": range_low,
            "range_high": range_high,
            "n_bins": n_bins,
        }
        self._run_worker("area_chart", params)

    def _start_job(self, action):
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请先添加输入文件")
            return

        multiwfn_exe = self.edit_mwfn.text()
        vmd_exe = self.edit_vmd.text()
        vmd_dir = self.edit_vmdir.text() or os.path.dirname(vmd_exe)

        if not os.path.isfile(multiwfn_exe):
            QMessageBox.critical(self, "错误", f"Multiwfn 不存在:\n{multiwfn_exe}")
            return
        if not os.path.isfile(vmd_exe):
            QMessageBox.critical(self, "错误", f"VMD 不存在:\n{vmd_exe}")
            return

        mode = self._get_current_mode()
        try:
            color_low = float(self.edit_clow.text())
            color_high = float(self.edit_chigh.text())
            pt_size = float(self.edit_ptsize.text())
        except ValueError:
            QMessageBox.critical(self, "错误", "色彩范围或点大小不是有效数字")
            return

        # Start callback server for preview mode (for pick events)
        self._start_callback_server()

        if action == "preview":
            self._set_pick_mode_ui(False)

        # Allocate VMD port in main thread so it's available immediately
        if action == "preview":
            self.vmd_port = find_free_port()

        params = {
            "multiwfn_exe": multiwfn_exe,
            "vmd_exe": vmd_exe,
            "vmd_dir": vmd_dir,
            "mode": mode,
            "color_low": color_low,
            "color_high": color_high,
            "pt_size": pt_size,
            "input_files": list(self.input_files),
            "work_dir": self.work_dir,
            "callback_port": self.callback_port,
            "vmd_port": self.vmd_port if action == "preview" else None,
        }

        self._run_worker(action, params)

    def _start_render_view_job(self, tachyon_exe, output_path, resolution):
        params = {
            "vmd_port": self.vmd_port,
            "vmd_render_dir": self.vmd_render_dir,
            "tachyon_exe": tachyon_exe,
            "output_path": output_path,
            "resolution": resolution,
        }
        self._run_worker("render_view", params)

    def _run_worker(self, action, params):
        # Clean up previous worker
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)

        self._worker = JobWorker(action, params)
        self._worker.log_signal.connect(self.log)
        self._worker.progress_signal.connect(self.set_progress)
        self._worker.status_signal.connect(self.set_status)
        self._worker.error_signal.connect(self._on_worker_error)
        self._worker.finished_signal.connect(self._on_worker_finished)

        if action in ("preview", "area_chart"):
            self._set_main_buttons_enabled(False)
        self._worker.start()

    def _on_worker_finished(self, action, result):
        self._set_main_buttons_enabled(True)

        if action == "preview":
            info = result
            self.vmd_port = info["vmd_port"]
            self.vmd_render_dir = info["vmd_render_dir"]
            vmd_exe = info["vmd_exe"]
            script = info["script"]
            mode = info["mode"]
            self.esp_unit = info.get("esp_unit", "kcal/mol")
            vmd_dir = self.edit_vmdir.text() or os.path.dirname(vmd_exe)

            self.vmd_process = launch_vmd(vmd_exe, script, vmd_dir)
            self.log("VMD 已启动，等待渲染服务就绪...")

            def wait_vmd_ready():
                ready = False
                for i in range(60):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(1)
                        s.connect(("127.0.0.1", self.vmd_port))
                        s.close()
                        ready = True
                        break
                    except (ConnectionRefusedError, OSError, socket.timeout):
                        time.sleep(0.5)
                if ready:
                    self.log("VMD 渲染服务已就绪，请在 VMD 窗口中调整视角")
                    self.log("调整好后点击 [📷 渲染当前视角] 按钮出图")
                    self.btn_render_view.setEnabled(True)
                    if mode in ("iso", "all"):
                        self.opacity_slider.setEnabled(True)
                    if mode in ("ext", "all"):
                        self.btn_pick.setEnabled(True)
                        self.log("EXT/ALL 模式：点击 [🔍 查询极值点] 可查看极值点 ESP 数值")
                else:
                    self.log("⚠ VMD 渲染服务启动超时，按钮仍不可用")

            threading.Thread(target=wait_vmd_ready, daemon=True).start()

        elif action == "render_view":
            try:
                os.startfile(result)
            except Exception:
                pass
            self.btn_render_view.setEnabled(True)

        elif action == "area_chart":
            for p in result:
                try:
                    os.startfile(p)
                except Exception:
                    pass

    def _on_worker_error(self, msg):
        self._set_main_buttons_enabled(True)
        if msg == "VMD 未启动":
            QMessageBox.warning(self, "提示", msg)
        else:
            QMessageBox.critical(self, "错误", msg)


# ── Main ───────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ESPGuiQt()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()