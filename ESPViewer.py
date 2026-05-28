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
    QMessageBox, QDialog, QButtonGroup, QSizePolicy, QSplitter
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
                           color_high=50.0, pt_size=4.0, show_colorbar=False,
                           colorbar_unit="kcal/mol"):
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
    if show_colorbar:
        tcl += "\n" + _generate_colorbar_tcl(color_low, color_high, colorbar_unit)
    script_path = os.path.join(vmd_dir, "esp_auto_pt.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystem=1, color_low=-0.03,
                            color_high=0.03, show_colorbar=False, colorbar_unit="a.u."):
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
    if show_colorbar:
        tcl += "\n" + _generate_colorbar_tcl(color_low, color_high, colorbar_unit)
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
                            color_low_iso=-0.03, color_high_iso=0.03,
                            show_colorbar=False, colorbar_unit="a.u."):
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

    if show_colorbar:
        tcl += "\n" + _generate_colorbar_tcl(
            color_low_iso, color_high_iso, colorbar_unit
        )

    script_path = os.path.join(vmd_dir, "esp_auto_all.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def _generate_colorbar_tcl(color_low, color_high, unit_label,
                            bar_x=10.0, bar_y=-4.0, bar_w=0.3, bar_h=6.0):
    lines = ["\n# === ESP Color Scale Bar ==="]
    nseg = 50
    nlabels = 6

    for i in range(nseg):
        f = i / (nseg - 1)
        if f <= 0.5:
            rr, gg, bb = 2.0 * f, 2.0 * f, 1.0
        else:
            rr, gg, bb = 1.0, 2.0 - 2.0 * f, 2.0 - 2.0 * f
        rr = max(0.0, min(1.0, rr))
        gg = max(0.0, min(1.0, gg))
        bb = max(0.0, min(1.0, bb))

        y0 = bar_y + f * bar_h
        y1 = y0 + bar_h / nseg
        lines.append(f'draw color {{{rr:.4f} {gg:.4f} {bb:.4f}}}')
        lines.append(
            f'draw rectangle "{{{bar_x:.3f} {y0:.3f} 0}}" '
            f'"{{{bar_x + bar_w:.3f} {y1:.3f} 0}}"'
        )

    lines.append('draw color white')
    lines.append(
        f'draw line "{{{bar_x} {bar_y} 0}}" '
        f'"{{{bar_x + bar_w} {bar_y} 0}}" width 2'
    )
    lines.append(
        f'draw line "{{{bar_x + bar_w} {bar_y} 0}}" '
        f'"{{{bar_x + bar_w} {bar_y + bar_h} 0}}" width 2'
    )
    lines.append(
        f'draw line "{{{bar_x + bar_w} {bar_y + bar_h} 0}}" '
        f'"{{{bar_x} {bar_y + bar_h} 0}}" width 2'
    )
    lines.append(
        f'draw line "{{{bar_x} {bar_y + bar_h} 0}}" '
        f'"{{{bar_x} {bar_y} 0}}" width 2'
    )

    lines.append('draw color black')
    for i in range(nlabels + 1):
        f = i / nlabels
        val = color_low + f * (color_high - color_low)
        ly = bar_y + f * bar_h
        lines.append(
            f'draw text "{{{bar_x + bar_w + 0.2:.3f} {ly:.3f} 0}}" '
            f'"{"{:.2f}".format(val)}" size 0.8 thickness 2'
        )

    lines.append(
        f'draw text "{{{bar_x + bar_w / 2:.3f} {bar_y + bar_h + 0.6:.3f} 0}}" '
        f'"ESP ({unit_label})" size 0.9 thickness 3'
    )

    return "\n".join(lines)


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


def render_headless(vmd_exe, script_path, output_path, vmd_dir):
    script_dir = os.path.dirname(script_path)
    render_tcl = os.path.join(script_dir, "_esp_render.tcl")
    out_forward = output_path.replace("\\", "/")
    script_forward = script_path.replace("\\", "/")
    with open(render_tcl, "w", encoding="utf-8") as f:
        f.write(f"""# Auto-generated render script
source {script_forward}
render TachyonInternal {out_forward} rawaam TachyonInternal
quit
""")
    cmd = f'"{vmd_exe}" -dispdev text -e "{render_tcl}"'
    result = subprocess.run(cmd, shell=True, cwd=vmd_dir,
                           capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        print(f"  Rendered  : {output_path}")
        return True
    else:
        print("  RENDER FAIL")
        for line in result.stderr.strip().split("\n")[-15:]:
            print(f"    | {line}")
        return False


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
                                 resolution=(2000, 1500)):
    for fn in ["vmdscene.dat", "_esp_render.bmp"]:
        fp = os.path.join(vmd_dir, fn)
        if os.path.exists(fp):
            os.remove(fp)

    resp = send_vmd_cmd(port, "render Tachyon vmdscene.dat")
    if resp and "ERROR" in resp:
        print(f"  VMD render failed: {resp}")
        return None

    dat_path = os.path.join(vmd_dir, "vmdscene.dat")
    for _ in range(30):
        if os.path.exists(dat_path) and os.path.getsize(dat_path) > 0:
            break
        time.sleep(0.5)
    else:
        print("  Timeout waiting for vmdscene.dat")
        return None

    bmp_name = "_esp_render.bmp"
    args = [
        tachyon_exe, "vmdscene.dat",
        "-format", "BMP", "-o", bmp_name,
        "-res", str(resolution[0]), str(resolution[1]),
        "-numthreads", "4", "-aasamples", "24",
        "-fullshade",
    ]
    try:
        subprocess.run(args, capture_output=True, cwd=vmd_dir, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    bmp_path = os.path.join(vmd_dir, bmp_name)
    if not os.path.exists(bmp_path):
        print("  Tachyon output not found")
        return None

    out = output_path
    if not out:
        out = os.path.join(vmd_dir, "esp_render.png")

    try:
        from PIL import Image
        img = Image.open(bmp_path)
        img.save(out)
    except ImportError:
        if not out.lower().endswith(".bmp"):
            out = os.path.splitext(out)[0] + ".bmp"
        shutil.copy2(bmp_path, out)

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
            elif self.action == "render":
                self._run_render()
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
            tachyon_exe, output_path, resolution=resolution
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
        show_colorbar = params["show_colorbar"]
        input_files = params["input_files"]
        work_dir = params["work_dir"]

        self._run_pipeline(
            "preview", multiwfn_exe, vmd_exe, vmd_dir, mode,
            color_low, color_high, pt_size, show_colorbar,
            input_files, work_dir
        )

    def _run_render(self):
        params = self.params
        multiwfn_exe = params["multiwfn_exe"]
        vmd_exe = params["vmd_exe"]
        vmd_dir = params["vmd_dir"]
        mode = params["mode"]
        color_low = params["color_low"]
        color_high = params["color_high"]
        pt_size = params["pt_size"]
        show_colorbar = params["show_colorbar"]
        input_files = params["input_files"]
        work_dir = params["work_dir"]
        output_path = params.get("output_path")

        self._run_pipeline(
            "render", multiwfn_exe, vmd_exe, vmd_dir, mode,
            color_low, color_high, pt_size, show_colorbar,
            input_files, work_dir, output_path=output_path
        )

    def _run_pipeline(self, action, multiwfn_exe, vmd_exe, vmd_dir, mode,
                      color_low, color_high, pt_size, show_colorbar,
                      input_files, work_dir, output_path=None):

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
                                           color_low, color_high, pt_size,
                                           show_colorbar=show_colorbar,
                                           colorbar_unit="kcal/mol")
        elif mode == "iso":
            script = generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high,
                                            show_colorbar=show_colorbar,
                                            colorbar_unit="a.u.")
        elif mode == "ext":
            script = generate_vmd_script_ext(multiwfn_exe, vmd_dir)
        elif mode == "all":
            script = generate_vmd_script_all(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high,
                                            show_colorbar=show_colorbar,
                                            colorbar_unit="a.u.")
            self.log("ALL 模式: 已生成组合脚本 (ISO 等值面 + EXT 极值点)")

        self.log(f"VMD 脚本: {script}")

        if action == "preview":
            vmd_port = find_free_port()
            callback_port = self.params.get("callback_port")
            socket_code = socket_tcl_snippet(vmd_port, callback_port)
            with open(script, "a", encoding="utf-8") as f:
                f.write(socket_code)

            self.log(f"启动 VMD 预览 (交互模式, 端口 {vmd_port})...")
            self.set_status("启动 VMD...")
            self.set_progress(100)
            self.finished_signal.emit("preview", {
                "vmd_port": vmd_port,
                "vmd_render_dir": vmd_dir,
                "vmd_exe": vmd_exe,
                "script": script,
                "mode": mode,
            })
        else:
            if not output_path:
                output_path = os.path.splitext(input_files[0])[0] + "_esp.tga"
            self.log(f"无头渲染 -> {output_path} ...")
            self.set_status(f"渲染中: {os.path.basename(output_path)}")
            self.set_progress(98)
            ok = render_headless(vmd_exe, script, output_path, vmd_dir)
            if ok:
                self.log(f"✓ 渲染完成: {output_path}")
                self.set_progress(100)
                self.set_status(f"✓ 渲染完成: {os.path.basename(output_path)}")
                self.finished_signal.emit("render", output_path)
            else:
                self.log("✗ 渲染失败")
                self.set_status("✗ 渲染失败")
                self.error_signal.emit("渲染失败，请查看日志")

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
        self.setFixedSize(320, 200)
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP Surface Visualization v1.0 (PyQt5)")
        self.setMinimumSize(800, 650)

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

        self._build_ui()
        self._apply_style()
        self.log("ESP Surface Visualization GUI v1.0 (PyQt5) 已启动")
        self.log(f"工作目录: {self.work_dir}")

    def closeEvent(self, event):
        self._stop_callback_server()
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
            QMainWindow { background-color: #f0f0f0; }
            QGroupBox { font-weight: bold; color: #555; border: 1px solid #ccc;
                        border-radius: 4px; margin-top: 10px; padding-top: 16px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 4px 12px; border: 1px solid #bbb; border-radius: 3px;
                          background-color: #e0e0e0; }
            QPushButton:hover { background-color: #d0d0d0; }
            QPushButton:pressed { background-color: #c0c0c0; }
            QListWidget { background-color: white; color: #333; font-family: Consolas;
                          font-size: 9pt; }
            QLineEdit { padding: 2px 4px; border: 1px solid #ccc; border-radius: 2px; }
            QComboBox { padding: 2px 4px; border: 1px solid #ccc; }
            QRadioButton { background: transparent; }
            QCheckBox { background: transparent; }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(10, 8, 10, 8)

        # ── Title ──
        title_lbl = QLabel("ESP Surface Visualization")
        title_lbl.setStyleSheet("font-size: 16pt; font-weight: bold; color: #333;")
        main_layout.addWidget(title_lbl)

        # ── Path Settings ──
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
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        btn_save.clicked.connect(self._save_paths)
        path_layout.addWidget(btn_save, 0, 3, 4, 1)

        main_layout.addWidget(path_grp)

        # ── Input Files ──
        in_grp = QGroupBox("输入文件 (.fch / .fchk)")
        in_layout = QVBoxLayout(in_grp)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加文件")
        btn_add.setStyleSheet("background-color: #2196F3; color: white;")
        btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(btn_add)

        btn_adddir = QPushButton("添加目录")
        btn_adddir.setStyleSheet("background-color: #2196F3; color: white;")
        btn_adddir.clicked.connect(self._add_dir)
        btn_row.addWidget(btn_adddir)

        btn_clear = QPushButton("清空列表")
        btn_clear.setStyleSheet("background-color: #f44336; color: white;")
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

        main_layout.addWidget(in_grp)

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
        self.combo_res.setMaximumWidth(100)
        set_layout.addWidget(self.combo_res, 2, 3)

        set_layout.addWidget(QLabel("输出路径:"), 3, 0)
        self.edit_output = QLineEdit()
        set_layout.addWidget(self.edit_output, 3, 1, 1, 3)
        btn_output = QPushButton("浏览")
        btn_output.clicked.connect(self._browse_output)
        set_layout.addWidget(btn_output, 3, 4)

        self.chk_colorbar = QCheckBox("显示色彩刻度轴 (Color Scale Bar)")
        self.chk_colorbar.setChecked(True)
        set_layout.addWidget(self.chk_colorbar, 4, 1, 1, 3)

        main_layout.addWidget(set_grp)

        # ── Action Buttons ──
        act_layout = QHBoxLayout()

        self.btn_preview = QPushButton("▶ VMD 预览")
        self.btn_preview.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 20px;"
        )
        self.btn_preview.clicked.connect(self._action_preview)
        act_layout.addWidget(self.btn_preview)

        self.btn_render_view = QPushButton("📷 渲染当前视角")
        self.btn_render_view.setEnabled(False)
        self.btn_render_view.setStyleSheet(
            "background-color: #FF9800; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 20px;"
        )
        self.btn_render_view.clicked.connect(self._action_render_view)
        act_layout.addWidget(self.btn_render_view)

        self.btn_pick = QPushButton("🔍 查询极值点")
        self.btn_pick.setEnabled(False)
        self.btn_pick.setStyleSheet(
            "background-color: #9C27B0; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 20px;"
        )
        self.btn_pick.clicked.connect(self._action_pick)
        act_layout.addWidget(self.btn_pick)

        self.btn_render_out = QPushButton("▼ 渲染输出")
        self.btn_render_out.setStyleSheet(
            "background-color: #2196F3; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 20px;"
        )
        self.btn_render_out.clicked.connect(self._action_render)
        act_layout.addWidget(self.btn_render_out)

        self.btn_area_chart = QPushButton("📊 ESP分区面积图")
        self.btn_area_chart.setStyleSheet(
            "background-color: #795548; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 20px;"
        )
        self.btn_area_chart.clicked.connect(self._action_area_chart)
        act_layout.addWidget(self.btn_area_chart)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setStyleSheet(
            "background-color: #f44336; color: white; font-size: 10pt; "
            "padding: 8px 15px;"
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
        self.lbl_status.setStyleSheet("color: #666; font-family: Consolas; font-size: 9pt;")
        main_layout.addWidget(self.lbl_status)

        # ── Log Window ──
        log_grp = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_grp)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(
            "background-color: #1e1e1e; color: #dcdcdc; "
            "font-family: Consolas; font-size: 9pt;"
        )
        log_layout.addWidget(self.txt_log)

        main_layout.addWidget(log_grp, 1)

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
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.callback_port))
            srv.listen(1)
            srv.settimeout(1.0)
            self.callback_server = srv
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
                            QTimer.singleShot(0, lambda m=msg: self._handle_pick(m))
                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception:
                    break

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
                self.log(
                    f"  📌 极值点 #{pdb_line} (index {atom_idx}): "
                    f"ESP = {beta_val:.2f} {unit} ({ext_type})"
                )
                self.log(f"     坐标: ({x}, {y}, {z})")
                self.set_status(
                    f"ESP = {beta_val:.2f} {unit} ({ext_type}, index {atom_idx})"
                )
            else:
                self.log(f"  📌 极值点 #{pdb_line} (index {atom_idx}): {ext_type}")
                self.log(f"     坐标: ({x}, {y}, {z})")
        except (IndexError, ValueError) as e:
            self.log(f"  ⚠ 解析 VMD pick 数据失败: {msg} ({e})")

    def _set_main_buttons_enabled(self, enabled):
        for btn in [self.btn_preview, self.btn_render_out, self.btn_area_chart]:
            btn.setEnabled(enabled)
        if enabled:
            self.btn_render_view.setEnabled(False)
            self.btn_pick.setEnabled(False)

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
            self, "保存渲染输出", "", "TGA (*.tga);;PNG (*.png);;BMP (*.bmp)"
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

    def _action_render(self):
        self._start_job("render")

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
        if not self.vmd_port:
            QMessageBox.warning(self, "提示", "请先点击 [▶ VMD 预览] 启动 VMD")
            return
        self._run_worker("pick", {"vmd_port": self.vmd_port})

    def _action_stop(self):
        os.system("taskkill /F /IM vmd.exe 2>nul")
        self.log("已发送停止信号")

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

        show_colorbar = self.chk_colorbar.isChecked()

        # Start callback server for preview mode
        self._start_callback_server()

        params = {
            "multiwfn_exe": multiwfn_exe,
            "vmd_exe": vmd_exe,
            "vmd_dir": vmd_dir,
            "mode": mode,
            "color_low": color_low,
            "color_high": color_high,
            "pt_size": pt_size,
            "show_colorbar": show_colorbar,
            "input_files": list(self.input_files),
            "work_dir": self.work_dir,
            "callback_port": self.callback_port,
        }
        if action == "render":
            params["output_path"] = self.edit_output.text() or None

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

        if action in ("preview", "render", "area_chart"):
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
            vmd_dir = self.edit_vmdir.text() or os.path.dirname(vmd_exe)

            self.vmd_process = launch_vmd(vmd_exe, script, vmd_dir)
            self.log("VMD 已启动，请在 VMD 窗口中调整视角")
            self.log("调整好后点击 [📷 渲染当前视角] 按钮出图")

            self.btn_render_view.setEnabled(True)
            if mode in ("ext", "all"):
                self.btn_pick.setEnabled(True)
                self.log("EXT/ALL 模式：点击 [🔍 查询极值点] 可查看极值点 ESP 数值")

        elif action == "render":
            QMessageBox.information(self, "完成", f"渲染完成:\n{result}")
            self.btn_render_view.setEnabled(False)
            self.btn_pick.setEnabled(False)

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