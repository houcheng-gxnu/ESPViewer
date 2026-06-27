#!/usr/bin/env python3
"""
ESP Surface Visualization GUI v2.0 (PyQt5 Edition)
Multiwfn (fchk -> surface data) + VMD (Preview + Tachyon Rendering)

Based on: sobereva.com/443 (Multiwfn+VMD ESP surface tutorial)

Features:
  - Select .fch/.fchk files (single or batch)
  - Mode: PT (vertex-colored), ISO (isosurface-colored), EXT (extrema), ALL (overlay)
  - Color scale range adjustment
  - VMD interactive preview or headless Tachyon render
  - Output path selection
  - VMD socket control (render current view, pick extrema)
  - ESP area distribution analysis with histograms

Usage:
  Run directly -> GUI pops up
  python esp_surface_gui.py
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
import math

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog, QDialog,
    QGroupBox, QGridLayout, QSpinBox, QMessageBox, QComboBox, QCheckBox,
    QDoubleSpinBox, QFrame, QScrollArea, QListWidget,
    QProgressBar, QRadioButton, QButtonGroup, QDialogButtonBox,
    QTabWidget, QSlider,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QTextCursor, QPainter, QColor, QKeySequence

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

AU = 627.509  # 1 a.u. = 627.509 kcal/mol

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

CMD_ESP_QUERY_RANGE = """
12
0
-1
-1
q
"""

# ── Progress Parsing ──────────────────────────────────────
_PROGRESS_PATTERNS = [
    (re.compile(r'(\d+(?:\.\d+)?)\s*%', re.IGNORECASE), 'pct'),
    (re.compile(r'(\d+)\s*/\s*(\d+)', re.IGNORECASE), 'frac'),
    (re.compile(r'(\d+)\s+of\s+(\d+)', re.IGNORECASE), 'frac'),
]


def _parse_progress(line):
    """Try to extract progress info from a Multiwfn output line.
    Returns (value: float 0.0-1.0 or None, msg: str).
    """
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


# ── ESP Range Auto-Detection ─────────────────────────────────

_RE_GLOBAL_MIN = re.compile(
    r'Global surface minimum:\s*(-?[\d.]+)\s*a\.u\.', re.IGNORECASE)
_RE_GLOBAL_MAX = re.compile(
    r'Global surface maximum:\s*(-?[\d.]+)\s*a\.u\.', re.IGNORECASE)
_RE_SUMMARY_MIN_MAX = re.compile(
    r'Minimal value:\s*(-?[\d.]+)\s*kcal/mol\s+Maximal value:\s*(-?[\d.]+)\s*kcal/mol',
    re.IGNORECASE)


def _parse_esp_range_from_stdout(stdout):
    """Extract ESP min/max from Multiwfn module 12 stdout.
    Returns (low, high, unit_str) or None.
    unit_str is 'kcal/mol' or 'a.u.' depending on which line was matched.
    """
    if not stdout:
        return None

    # Try summary line first (kcal/mol) - available when Start analysis is run
    m = _RE_SUMMARY_MIN_MAX.search(stdout)
    if m:
        return (float(m.group(1)), float(m.group(2)), 'kcal/mol')

    # Fallback: Global min/max (a.u.)
    esp_min = None
    esp_max = None
    for line in stdout.splitlines():
        if esp_min is None:
            m = _RE_GLOBAL_MIN.search(line)
            if m:
                esp_min = float(m.group(1))
        if esp_max is None:
            m = _RE_GLOBAL_MAX.search(line)
            if m:
                esp_max = float(m.group(1))
        if esp_min is not None and esp_max is not None:
            return (esp_min, esp_max, 'a.u.')

    return None


def run_multiwfn(multiwfn_exe, fch_path, cmd_string, work_dir, extra_args="",
                 progress_cb=None):
    """Run Multiwfn with command string via file redirection.
    Returns (success: bool, stdout: str)
    """
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
    print(f"  Multiwfn: {cmd}")

    try:
        proc = subprocess.Popen(
            cmd, shell=True,
            cwd=work_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except Exception as e:
        print(f"    Popen failed: {e}")
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
        print("    TIMEOUT waiting for Multiwfn exit")

    full_stdout = "".join(stdout_lines)

    if proc.returncode not in (0, 24):
        print(f"    WARN: exit code {proc.returncode}")
        for line in full_stdout.strip().split("\n")[-15:]:
            print(f"      | {line}")
        return False, full_stdout

    if "Error" in full_stdout or "ERROR" in full_stdout:
        print("    WARN: 'Error' in stdout")
        for line in full_stdout.strip().split("\n")[-10:]:
            print(f"      | {line}")

    return True, full_stdout


# ── VMD Script Generators ─────────────────────────────────

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
    """Generate VMD TCL script for PT mode."""
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
        tcl += "\n# color scale bar will be sent via TCP after VMD starts"
    script_path = os.path.join(vmd_dir, "esp_auto_pt.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystem=1, color_low=-0.03,
                            color_high=0.03, show_colorbar=False,
                            colorbar_unit="a.u."):
    """Generate VMD TCL script for ISO mode.
    color_low/color_high are expected in kcal/mol; converted to a.u. for VMD.
    """
    iso_low = color_low / AU
    iso_high = color_high / AU
    orig = _read_original_vmd(multiwfn_exe, "ESPiso.vmd")
    if orig:
        tcl = orig
        tcl = tcl.replace("set nsystem 1", f"set nsystem {nsystem}")
        tcl = tcl.replace("set colorlow -0.03", f"set colorlow {iso_low}")
        tcl = tcl.replace("set colorhigh 0.03", f"set colorhigh {iso_high}")
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
set colorlow {iso_low}
set colorhigh {iso_high}

for {{set i 1}} {{$i<=$nsystem}} {{incr i}} {{
    mol new density$i.cub
    mol addfile ESP$i.cub
    set id [molinfo top get id]
    mol modstyle 0 $id CPK 1.000000 0.300000 22.000000 22.000000
    mol addrep $id
    mol modstyle 1 $id Isosurface 0.001000 0 0 0 1 1
    mol modmaterial 1 $id EdgyGlass
    mol modcolor 1 $id Volume 1
    mol scaleminmax $id 1 $colorlow $colorhigh
    display resetview
}}
display resetview
"""
    if show_colorbar:
        tcl += "\n# color scale bar will be sent via TCP after VMD starts"
    script_path = os.path.join(vmd_dir, "esp_auto_iso.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


def generate_vmd_script_ext(multiwfn_exe, vmd_dir):
    """Generate VMD TCL script for EXT mode."""
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
    """Generate combined VMD TCL script for ALL mode (ISO + EXT).
    color_low_iso/color_high_iso are expected in kcal/mol; converted to a.u. for VMD.
    """
    iso_low = color_low_iso / AU
    iso_high = color_high_iso / AU
    iso_tcl = _read_original_vmd(multiwfn_exe, "ESPiso.vmd")
    if iso_tcl:
        iso_tcl = iso_tcl.replace("set nsystem 1", f"set nsystem {nsystem}")
        iso_tcl = iso_tcl.replace("set colorlow -0.03", f"set colorlow {iso_low}")
        iso_tcl = iso_tcl.replace("set colorhigh 0.03", f"set colorhigh {iso_high}")
        iso_tcl = iso_tcl.replace("set colorlow -0.8", f"#set colorlow -0.8")
        iso_tcl = iso_tcl.replace("set colorhigh 0.8", f"#set colorhigh -0.8")
    else:
        iso_tcl = f"""#This script is used to draw ESP colored molecular vdW surface (rho=0.001)
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
set colorlow {iso_low}
set colorhigh {iso_high}

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
        tcl += "\n# color scale bar will be sent via TCP after VMD starts"

    script_path = os.path.join(vmd_dir, "esp_auto_all.vmd")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(tcl)
    return script_path


# ── ESP Area Distribution Analysis ─────────────────────────

def run_multiwfn_area_dist(multiwfn_exe, fch_path, work_dir,
                            range_low=-25.0, range_high=22.0,
                            n_bins=15, unit_code=3, progress_cb=None,
                            log_cb=None):
    """Run Multiwfn area distribution analysis for a single .fch file."""
    cmd = CMD_ESP_AREA_DIST + f"{range_low},{range_high}\n{n_bins}\n{unit_code}\nn\n"
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
    """Parse Multiwfn area distribution output from stdout."""
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
    """Generate ESP area distribution histogram as PNG."""
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

    fig, ax = plt.subplots(figsize=(12, 7))
    bars = ax.bar(
        centers, areas, width=bin_width * 0.92, align='center',
        color=colors, edgecolor='#333333', linewidth=0.8,
    )
    ax.set_xlabel(xlabel, fontsize=12)
    ax.set_ylabel('Surface Area (Å²)', fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold', pad=12)
    ax.set_xticks(centers)
    ax.set_xticklabels([f'{c:.1f}' for c in centers], rotation=45, ha='right', fontsize=8)

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
    """Export ESP area distribution data as tab-separated text file."""
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


# ── Interactive Area Chart Dialog ──────────────────────────

class AreaChartDialog(QDialog):
    """Interactive matplotlib chart dialog with editable title/labels."""

    def __init__(self, all_data, parent=None):
        super().__init__(parent)
        self.all_data = all_data  # [(fname, [(center,area,pct),...]), ...]
        self.current_idx = 0
        self._overlay_mode = False
        self._chart_type = "bar"  # "bar" or "line"
        self._canvas = None
        self._ax = None
        self._fig = None

        self.setWindowTitle("ESP 分区面积图 — 交互预览")
        self.setMinimumSize(700, 750)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")

        layout = QVBoxLayout()

        # ── Top controls ──
        top = QHBoxLayout()

        top.addWidget(QLabel("标题:"))
        self.title_edit = QLineEdit("ESP Area Distribution")
        self.title_edit.setPlaceholderText("图表标题")
        self.title_edit.textChanged.connect(self._refresh_chart)
        top.addWidget(self.title_edit, stretch=2)

        top.addWidget(QLabel("X轴:"))
        self.xlabel_edit = QLineEdit("Electrostatic potential (kcal/mol)")
        self.xlabel_edit.setPlaceholderText("X轴标签")
        self.xlabel_edit.textChanged.connect(self._refresh_chart)
        top.addWidget(self.xlabel_edit, stretch=1)

        top.addWidget(QLabel("Y轴:"))
        self.ylabel_edit = QLineEdit("Surface Area (Å²)")
        self.ylabel_edit.setPlaceholderText("Y轴标签")
        self.ylabel_edit.textChanged.connect(self._refresh_chart)
        top.addWidget(self.ylabel_edit, stretch=1)

        if len(all_data) > 1:
            top.addWidget(QLabel("文件:"))
            self.file_combo = QComboBox()
            self.file_combo.addItems([d[0] for d in all_data])
            self.file_combo.currentIndexChanged.connect(self._on_file_changed)
            top.addWidget(self.file_combo)

            self._overlay_cb = QCheckBox("叠加对比")
            self._overlay_cb.stateChanged.connect(self._on_overlay_toggled)
            top.addWidget(self._overlay_cb)

            self._chart_type_combo = QComboBox()
            self._chart_type_combo.addItems(["柱状图", "折线图"])
            self._chart_type_combo.setEnabled(False)
            self._chart_type_combo.currentIndexChanged.connect(self._on_chart_type_changed)
            top.addWidget(self._chart_type_combo)

        layout.addLayout(top)

        # ── Font/style controls ──
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("坐标字号:"))
        self.tick_fs_spin = QSpinBox()
        self.tick_fs_spin.setRange(6, 20)
        self.tick_fs_spin.setValue(9)
        self.tick_fs_spin.setMaximumWidth(60)
        self.tick_fs_spin.valueChanged.connect(self._refresh_chart)
        ctrl_row.addWidget(self.tick_fs_spin)

        ctrl_row.addWidget(QLabel("数值字号:"))
        self.bar_fs_spin = QSpinBox()
        self.bar_fs_spin.setRange(5, 18)
        self.bar_fs_spin.setValue(7)
        self.bar_fs_spin.setMaximumWidth(60)
        self.bar_fs_spin.valueChanged.connect(self._refresh_chart)
        ctrl_row.addWidget(self.bar_fs_spin)

        self._show_bar_val_cb = QCheckBox("显示数值")
        self._show_bar_val_cb.setChecked(True)
        self._show_bar_val_cb.stateChanged.connect(self._refresh_chart)
        ctrl_row.addWidget(self._show_bar_val_cb)

        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # ── Matplotlib canvas ──
        try:
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT

            # Paper-style: Arial font, black axes border
            plt.rcParams['font.family'] = 'Arial'
            plt.rcParams['axes.edgecolor'] = 'black'
            plt.rcParams['axes.linewidth'] = 1.2
            self._fig, self._ax = plt.subplots(figsize=(7, 7))
            self._canvas = FigureCanvasQTAgg(self._fig)
            self._canvas.setMinimumHeight(450)
            layout.addWidget(self._canvas)

            toolbar = NavigationToolbar2QT(self._canvas, self)
            layout.addWidget(toolbar)
        except ImportError:
            layout.addWidget(QLabel("(matplotlib 交互后端不可用)"))

        # ── Bottom buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("💾 保存图片")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background: #1E88E5; color: white; font-weight: bold; "
            "padding: 8px 20px; border-radius: 6px; border: none; }"
            "QPushButton:hover { background: #1976D2; }"
        )
        save_btn.clicked.connect(self._save_chart)
        btn_row.addWidget(save_btn)

        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

        self._refresh_chart()

    def _on_overlay_toggled(self, state):
        self._overlay_mode = bool(state)
        self.file_combo.setEnabled(not self._overlay_mode)
        self._chart_type_combo.setEnabled(self._overlay_mode)
        self._refresh_chart()

    def _on_chart_type_changed(self, idx):
        self._chart_type = "bar" if idx == 0 else "line"
        self._refresh_chart()

    def _on_file_changed(self, idx):
        self.current_idx = idx
        self._refresh_chart()

    def _refresh_chart(self):
        if self._ax is None:
            return
        self._ax.clear()

        if self._overlay_mode and len(self.all_data) > 1:
            if self._chart_type == "line":
                self._draw_overlay_line()
            else:
                self._draw_overlay()
        else:
            self._draw_single()

        self._fig.tight_layout()
        self._canvas.draw_idle()

    def _draw_single(self):
        _, data = self.all_data[self.current_idx]

        centers = [d[0] for d in data]
        areas = [d[1] for d in data]

        if len(centers) > 1:
            bin_width = centers[1] - centers[0]
        else:
            bin_width = 2.0

        data_min, data_max = min(centers), max(centers)
        pad = max(0.5, (data_max - data_min) * 0.05)
        norm = mcolors.TwoSlopeNorm(vmin=data_min - pad, vcenter=0, vmax=data_max + pad)
        cmap_bwr = plt.get_cmap('bwr')
        colors = [cmap_bwr(norm(c)) for c in centers]

        bars = self._ax.bar(
            centers, areas, width=bin_width * 0.92, align='center',
            color=colors, edgecolor='#333333', linewidth=0.8,
        )
        self._ax.set_xlabel(self.xlabel_edit.text() or "ESP", fontsize=12)
        self._ax.set_ylabel(self.ylabel_edit.text() or "Area", fontsize=12)
        self._ax.set_title(self.title_edit.text() or "Area Distribution",
                           fontsize=14, fontweight='bold', pad=12)
        self._ax.set_xticks(centers)
        tick_fs = self.tick_fs_spin.value()
        self._ax.set_xticklabels([f'{c:.1f}' for c in centers], rotation=45, ha='right', fontsize=tick_fs)
        self._ax.tick_params(axis='y', labelsize=tick_fs)

        max_area = max(areas) if max(areas) > 0 else 1
        if self._show_bar_val_cb.isChecked():
            bar_fs = self.bar_fs_spin.value()
            for bar, val in zip(bars, areas):
                if val > 0:
                    self._ax.text(
                        bar.get_x() + bar.get_width() / 2.,
                        bar.get_height() + max_area * 0.02,
                        f'{val:.1f}', ha='center', va='bottom',
                        fontsize=bar_fs, rotation=90, color='#333333',
                    )

        self._ax.spines['top'].set_visible(True)
        self._ax.spines['right'].set_visible(True)
        self._ax.set_xlim(centers[0] - bin_width, centers[-1] + bin_width)
        self._ax.set_ylim(0, max_area * 1.18)

    def _draw_overlay(self):
        """Grouped bar chart: all molecules overlaid with distinct colors."""
        # Collect all data and determine common centers
        all_centers = set()
        for _, data in self.all_data:
            for d in data:
                all_centers.add(round(d[0], 2))
        centers = sorted(all_centers)
        n_centers = len(centers)

        if n_centers > 1:
            bin_width = centers[1] - centers[0]
        else:
            bin_width = 2.0

        n_mols = len(self.all_data)

        # Distinct colors per molecule
        mol_colors = ['#E53935', '#1E88E5', '#43A047', '#FB8C00',
                       '#8E24AA', '#00ACC1', '#F4511E', '#3949AB']
        total_width = bin_width * 0.85
        bar_w = total_width / n_mols

        data_min = min(centers)
        data_max = max(centers)

        all_bars = []
        max_val = 0

        for mi, (fname, data) in enumerate(self.all_data):
            # Build lookup
            data_map = {}
            for d in data:
                data_map[round(d[0], 2)] = d[1]

            areas = [data_map.get(c, 0.0) for c in centers]
            max_val = max(max_val, max(areas) if areas else 0)

            offset = (mi - (n_mols - 1) / 2) * bar_w
            pos = [c + offset for c in centers]

            # Short label for legend
            short_name = os.path.splitext(fname)[0]
            if len(short_name) > 25:
                short_name = short_name[:24] + "…"

            bars = self._ax.bar(
                pos, areas, width=bar_w * 0.92, align='center',
                color=mol_colors[mi % len(mol_colors)],
                edgecolor='#222222', linewidth=0.6,
                label=short_name,
            )
            all_bars.append(bars)

        self._ax.set_xlabel(self.xlabel_edit.text() or "ESP", fontsize=12)
        self._ax.set_ylabel(self.ylabel_edit.text() or "Area", fontsize=12)
        self._ax.set_title(self.title_edit.text() or "Area Distribution",
                           fontsize=14, fontweight='bold', pad=12)
        self._ax.set_xticks(centers)
        tick_fs = self.tick_fs_spin.value()
        self._ax.set_xticklabels([f'{c:.1f}' for c in centers], rotation=45, ha='right', fontsize=tick_fs)
        self._ax.tick_params(axis='y', labelsize=tick_fs)

        if self._show_bar_val_cb.isChecked():
            bar_fs = self.bar_fs_spin.value()
            for bars in all_bars:
                for bar in bars:
                    val = bar.get_height()
                    if val > 0:
                        self._ax.text(
                            bar.get_x() + bar.get_width() / 2.,
                            val + max_val * 0.01,
                            f'{val:.1f}', ha='center', va='bottom',
                            fontsize=bar_fs, rotation=90, color='#333333',
                        )

        if max_val > 0:
            self._ax.set_ylim(0, max_val * 1.22)

        pad = max(0.5, (data_max - data_min) * 0.05)
        self._ax.set_xlim(centers[0] - bin_width * 0.6, centers[-1] + bin_width * 0.6)

        self._ax.spines['top'].set_visible(True)
        self._ax.spines['right'].set_visible(True)

        # Legend
        if n_mols <= 8:
            self._ax.legend(loc='upper right', fontsize=9, framealpha=0.9)

    def _draw_overlay_line(self):
        """Line chart overlay: smooth lines for multi-molecule comparison."""
        all_centers = set()
        for _, data in self.all_data:
            for d in data:
                all_centers.add(round(d[0], 2))
        centers = sorted(all_centers)

        mol_colors = ['#E53935', '#1E88E5', '#43A047', '#FB8C00',
                       '#8E24AA', '#00ACC1', '#F4511E', '#3949AB']
        markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p']

        n_mols = len(self.all_data)
        max_val = 0

        data_min, data_max = min(centers), max(centers)

        for mi, (fname, data) in enumerate(self.all_data):
            data_map = {}
            for d in data:
                data_map[round(d[0], 2)] = d[1]

            areas = [data_map.get(c, 0.0) for c in centers]
            max_val = max(max_val, max(areas) if areas else 0)

            short_name = os.path.splitext(fname)[0]
            if len(short_name) > 25:
                short_name = short_name[:24] + "…"

            color = mol_colors[mi % len(mol_colors)]
            marker = markers[mi % len(markers)]

            self._ax.plot(
                centers, areas, color=color, marker=marker,
                linewidth=2.2, markersize=7, label=short_name,
                markeredgecolor='#222222', markeredgewidth=0.5,
            )

            if self._show_bar_val_cb.isChecked():
                bar_fs = self.bar_fs_spin.value()
                for cx, ay in zip(centers, areas):
                    if ay > 0:
                        self._ax.annotate(
                            f'{ay:.1f}', (cx, ay),
                            textcoords="offset points", xytext=(0, 7),
                            ha='center', fontsize=bar_fs, color='#333333',
                        )

        self._ax.set_xlabel(self.xlabel_edit.text() or "ESP", fontsize=12)
        self._ax.set_ylabel(self.ylabel_edit.text() or "Area", fontsize=12)
        self._ax.set_title(self.title_edit.text() or "Area Distribution",
                           fontsize=14, fontweight='bold', pad=12)
        tick_fs = self.tick_fs_spin.value()
        self._ax.set_xticks(centers)
        self._ax.set_xticklabels([f'{c:.1f}' for c in centers], rotation=45, ha='right', fontsize=tick_fs)
        self._ax.tick_params(axis='y', labelsize=tick_fs)

        pad = max(0.5, (data_max - data_min) * 0.05)
        self._ax.set_xlim(data_min - pad, data_max + pad)

        if max_val > 0:
            self._ax.set_ylim(0, max_val * 1.18)

        self._ax.spines['top'].set_visible(True)
        self._ax.spines['right'].set_visible(True)

        if n_mols <= 8:
            self._ax.legend(loc='upper right', fontsize=9, framealpha=0.9)

    def _save_chart(self):
        fname = self.all_data[self.current_idx][0]
        default_name = os.path.splitext(fname)[0] + "_esp_area.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存面积图", default_name,
            "PNG (*.png);;JPG (*.jpg);;PDF (*.pdf)"
        )
        if path:
            try:
                self._fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='white')
                QMessageBox.information(self, "完成", f"已保存:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败:\n{e}")


# ── VMD Socket / Launch / Render ───────────────────────────

def find_free_port():
    """Find an available TCP port on localhost."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def socket_tcl_snippet(port, callback_port=0):
    """Generate Tcl code for a socket server inside VMD."""
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

    label_proc = (
        "\n# === One-click extremum label display ===\n"
        "# Helper: locate molid of surfanalysis.pdb once, so that all subsequent\n"
        "# 'graphics' operations target the extremum molecule explicitly rather\n"
        "# than relying on VMD's 'top' molecule (which can be changed by the user\n"
        "# in the VMD main window, breaking label cleanup).\n"
        "proc _esp_find_sa_molid {} {\n"
        "    foreach m [molinfo list] {\n"
        "        set molname [molinfo $m get name]\n"
        "        if {[string match {*surfanalysis.pdb} $molname]} {\n"
        "            return $m\n"
        "        }\n"
        "    }\n"
        "    return -1\n"
        "}\n"
        "proc show_extrema_labels {{size 1.2} {offset 0.8}} {\n"
        "    set molid [_esp_find_sa_molid]\n"
        "    if {$molid < 0} {\n"
        "        puts {ERROR: surfanalysis.pdb not found}\n"
        "        return\n"
        "    }\n"
        "    graphics $molid delete all\n"
        "    set sel [atomselect $molid all]\n"
        "    set coords [$sel get {x y z}]\n"
        "    set names  [$sel get name]\n"
        "    set betas  [$sel get beta]\n"
        "    set cx 0; set cy 0; set cz 0\n"
        "    set n [llength $coords]\n"
        "    foreach xyz $coords {\n"
        "        set cx [expr {$cx + [lindex $xyz 0]}]\n"
        "        set cy [expr {$cy + [lindex $xyz 1]}]\n"
        "        set cz [expr {$cz + [lindex $xyz 2]}]\n"
        "    }\n"
        "    set cx [expr {$cx / $n}]\n"
        "    set cy [expr {$cy / $n}]\n"
        "    set cz [expr {$cz / $n}]\n"
        "    $sel delete\n"
        "    foreach xyz $coords nm $names bt $betas {\n"
        "        set dx [expr {[lindex $xyz 0] - $cx}]\n"
        "        set dy [expr {[lindex $xyz 1] - $cy}]\n"
        "        set dz [expr {[lindex $xyz 2] - $cz}]\n"
        "        set norm [expr {sqrt($dx*$dx + $dy*$dy + $dz*$dz)}]\n"
        "        if {$norm > 0.001} {\n"
        "            set x [expr {[lindex $xyz 0] + $dx/$norm * $offset}]\n"
        "            set y [expr {[lindex $xyz 1] + $dy/$norm * $offset}]\n"
        "            set z [expr {[lindex $xyz 2] + $dz/$norm * $offset}]\n"
        "        } else {\n"
        "            set x [expr {[lindex $xyz 0] + $offset}]\n"
        "            set y [lindex $xyz 1]\n"
        "            set z [lindex $xyz 2]\n"
        "        }\n"
        "        if {$nm eq {C}} {\n"
        "            graphics $molid color red\n"
        "        } else {\n"
        "            graphics $molid color blue\n"
        "        }\n"
        "        graphics $molid text [list $x $y $z] [format {%.2f} $bt] size $size thickness 2\n"
        "    }\n"
        "    puts {Done.}\n"
        "}\n"
        "proc clear_extrema_labels {{}} {\n"
        "    set molid [_esp_find_sa_molid]\n"
        "    if {$molid < 0} {\n"
        "        puts {ERROR: surfanalysis.pdb not found}\n"
        "        return\n"
        "    }\n"
        "    graphics $molid delete all\n"
        "    puts {Labels cleared}\n"
        "}\n"
        "proc hide_extrema_points {{}} {\n"
        "    set molid [_esp_find_sa_molid]\n"
        "    if {$molid < 0} {\n"
        "        puts {ERROR: surfanalysis.pdb not found}\n"
        "        return\n"
        "    }\n"
        "    mol off $molid\n"
        "    graphics $molid delete all\n"
        "    puts {Extrema points hidden}\n"
        "}\n"
        "proc show_extrema_points {{}} {\n"
        "    set molid [_esp_find_sa_molid]\n"
        "    if {$molid < 0} {\n"
        "        puts {ERROR: surfanalysis.pdb not found}\n"
        "        return\n"
        "    }\n"
        "    mol on $molid\n"
        "    puts {Extrema points shown}\n"
        "}\n"
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
{pick_code}{label_proc}"""


def send_vmd_cmd(port, cmd, timeout=5):
    """Send a Tcl command to VMD via socket and return the response."""
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
                                 resolution=(2000, 1500), nthreads=14):
    """Connect to VMD, render current view via Tachyon, then convert to image."""
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
        "-numthreads", str(nthreads), "-aasamples", "24",
        "-fullshade",
    ]
    try:
        subprocess.run(args, capture_output=True, cwd=vmd_dir, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  Tachyon failed: {e}")
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


# ═══════════════════════════════════════════════════════════════════
#  StatusButton — capsule-status button with breathing animation
# ═══════════════════════════════════════════════════════════════════

class StatusButton(QPushButton):
    """Capsule status button, breathing pulse animation when running."""

    def __init__(self, text="Ready", parent=None):
        super().__init__(text, parent)
        self._state_text = text
        self._state_color = QColor("#1565C0")
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._running = False
        self._breath_color = QColor("#1565C0")
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style(self._state_color.name())

    def _apply_style(self, bg):
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background: {bg}; color: white; font-weight: bold; font-size: 11pt;"
            f"  padding: 8px 34px; border-radius: 20px; border: none;"
            f"}}"
        )

    def _tick(self):
        self._offset = (self._offset + 0.02) % 2.0
        self.update()

    def set_status(self, text, bg):
        self._state_text = text
        self._state_color = QColor(bg)
        self.stop()
        self.setText(text)
        self._apply_style(bg)

    def start(self, text="计算中"):
        if not self._running:
            self._running = True
            self._state_text = text
            self.setText(text)
            self._timer.start(16)

    def stop(self):
        if self._running:
            self._running = False
            self._timer.stop()
            self._apply_style(self._state_color.name())

    def paintEvent(self, event):
        if self._running:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            alpha = int(100 + 100 * (0.5 + 0.5 * math.sin(self._offset * math.pi)))
            c = QColor(self._breath_color)
            c.setAlpha(alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(0, 0, w, h, 20, 20)
            p.setPen(QColor("white"))
            f = p.font(); f.setBold(True); f.setPointSizeF(11)
            p.setFont(f)
            p.drawText(0, 0, w, h, Qt.AlignCenter, self._state_text)
            p.end()
        else:
            super().paintEvent(event)


# ═══════════════════════════════════════════════════════════════════
#  Workers
# ═══════════════════════════════════════════════════════════════════

class MultiwfnWorker(QThread):
    """Background thread for running Multiwfn + VMD jobs."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)  # result dict
    busy_signal = pyqtSignal(bool)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self._abort_flag = False

    def abort(self):
        self._abort_flag = True

    def run(self):
        try:
            self._do_run()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.log_signal.emit(f"[FATAL] 内部错误: {e}")
            self.log_signal.emit(tb)
            self.busy_signal.emit(False)
            self.finished_signal.emit({'ok': False, 'action': 'error', 'error': str(e)})

    def _do_run(self):
        params = self.params
        action = params['action']
        mode = params['mode']
        input_files = params['input_files']
        multiwfn_exe = params['multiwfn_exe']
        vmd_exe = params['vmd_exe']
        vmd_dir = params['vmd_dir']
        color_low = params['color_low']
        color_high = params['color_high']
        pt_size = params['pt_size']
        show_colorbar = params['show_colorbar']
        output_path = params.get('output_path', None)
        work_dir = params['work_dir']
        callback_port = params.get('callback_port', 0)
        nthreads = params.get('nthreads', 14)

        self.busy_signal.emit(True)

        # Write Multiwfn settings.ini with nthreads
        self._apply_multiwfn_nthreads(multiwfn_exe, nthreads)

        # Clean old files
        self._clean_vmd_dir(vmd_dir)

        nsystems = len(input_files)
        if mode == "all":
            steps_per_file = 3
        else:
            steps_per_file = 1
        total_steps = nsystems * steps_per_file
        completed_steps = 0

        self.status_signal.emit("开始计算...")
        self.progress_signal.emit(0)

        for idx, fch_path in enumerate(input_files):
            if self._abort_flag:
                self.log_signal.emit("[ABORT] 用户终止")
                break

            sys_num = idx + 1
            self.log_signal.emit(f"\n{'='*50}")
            self.log_signal.emit(f"处理文件 {sys_num}/{nsystems}: {os.path.basename(fch_path)}")
            self.log_signal.emit(f"模式: {mode}")

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
                    self.status_signal.emit(f"[文件 {file_idx}/{total_files}] {msg}")
                    if pct_val is not None:
                        step_base = (completed_val / total_val) * 100
                        step_range = (1 / total_val) * 100
                        overall = step_base + pct_val * step_range
                        self.progress_signal.emit(int(overall))
                    else:
                        step_base = (completed_val / total_val) * 100
                        step_range = (1 / total_val) * 100
                        self.progress_signal.emit(int(step_base + step_range * 0.5))
                return cb

            success = False
            if mode in ("pt", "all"):
                step_label = "ESPpt" if mode == "pt" else "ALL-PT"
                self.log_signal.emit(f"[1/{steps_per_file}] Multiwfn {step_label}...")
                cb = make_progress_cb(sys_num, nsystems, completed_steps, total_steps)
                ok, _ = run_multiwfn(multiwfn_exe, fch_in_tmp, CMD_ESPPT, tmp_dir, progress_cb=cb)
                if ok:
                    for fn in ("mol.pdb", "vtx.pdb"):
                        src = os.path.join(tmp_dir, fn)
                        if os.path.exists(src):
                            dest_fn = fn.replace(".pdb", f"{sys_num}.pdb")
                            shutil.copy2(src, os.path.join(vmd_dir, dest_fn))
                    self.log_signal.emit("  ESPpt OK")
                    success = True
                else:
                    self.log_signal.emit("  ESPpt FAILED")
                completed_steps += 1

            if mode in ("iso", "all"):
                step_label = "ESPiso" if mode == "iso" else "ALL-ISO"
                self.log_signal.emit(f"[ISO] Multiwfn {step_label}...")
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
                        iso_ok = False
                    if os.path.exists(src_totesp):
                        shutil.copy2(src_totesp, os.path.join(vmd_dir, f"ESP{sys_num}.cub"))
                    else:
                        iso_ok = False
                    if iso_ok:
                        self.log_signal.emit("  ESPiso OK")
                        success = True
                    else:
                        self.log_signal.emit("  ESPiso FAILED - missing output files")
                else:
                    self.log_signal.emit("  ESPiso FAILED")
                completed_steps += 1

            if mode in ("ext", "all"):
                step_label = "ESPext" if mode == "ext" else "ALL-EXT"
                self.log_signal.emit(f"[EXT] Multiwfn {step_label}...")
                cb = make_progress_cb(sys_num, nsystems, completed_steps, total_steps)
                ok, _ = run_multiwfn(multiwfn_exe, fch_in_tmp, CMD_ESPEXT, tmp_dir, progress_cb=cb)
                if ok:
                    for fn in ("mol.pdb", "vtx.pdb", "surfanalysis.pdb"):
                        src = os.path.join(tmp_dir, fn)
                        if os.path.exists(src):
                            shutil.copy2(src, os.path.join(vmd_dir, fn))
                    self.log_signal.emit("  ESPext OK")
                    success = True
                    sa_pdb = os.path.join(vmd_dir, "surfanalysis.pdb")
                    if os.path.exists(sa_pdb):
                        self._detect_esp_unit(sa_pdb)
                else:
                    self.log_signal.emit("  ESPext FAILED")
                completed_steps += 1

        # Generate VMD script
        self.log_signal.emit(f"\n{'='*50}")
        self.log_signal.emit("生成 VMD 脚本...")
        self.status_signal.emit("生成 VMD 脚本...")
        self.progress_signal.emit(95)

        if mode == "pt":
            script = generate_vmd_script_pt(multiwfn_exe, vmd_dir, nsystems,
                                           color_low, color_high, pt_size,
                                           show_colorbar=show_colorbar, colorbar_unit="kcal/mol")
        elif mode == "iso":
            script = generate_vmd_script_iso(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high,
                                            show_colorbar=show_colorbar, colorbar_unit="a.u.")
        elif mode == "ext":
            script = generate_vmd_script_ext(multiwfn_exe, vmd_dir)
        elif mode == "all":
            script = generate_vmd_script_all(multiwfn_exe, vmd_dir, nsystems,
                                            color_low, color_high,
                                            show_colorbar=show_colorbar, colorbar_unit="a.u.")
            self.log_signal.emit("ALL 模式: 已生成组合脚本 (ISO 等值面 + EXT 极值点)")
        else:
            return

        self.log_signal.emit(f"VMD 脚本: {script}")

        if action == "preview":
            vmd_port = find_free_port()
            socket_code = socket_tcl_snippet(vmd_port, callback_port)
            with open(script, "a", encoding="utf-8") as f:
                f.write(socket_code)

            # Append colorbar Tcl if enabled
            if show_colorbar:
                cb_tcl = f"""
# ── ESP Color Scale Bar ──
after 3000 {{
  if {{[catch {{
    package require colorscalebar
    catch {{::ColorScaleBar::delete_color_scale_bar}}
    ::ColorScaleBar::color_scale_bar \\
      1.5 \\
      0.08 \\
      0 \\
      1 \\
      {color_low} \\
      {color_high} \\
      10 \\
      16 \\
      0 \\
      0.82 \\
      -0.75 \\
      1 \\
      top \\
      0 \\
      1 \\
      "ESP (kcal/mol)"
  }} err]}} {{
    puts "ESPViewer ColorScaleBar ERROR:"
    puts $err
  }}
}}
"""
                with open(script, "a", encoding="utf-8") as f:
                    f.write(cb_tcl)
                self.log_signal.emit("色彩刻度轴将随 VMD 启动自动创建")
            self.log_signal.emit(f"启动 VMD 预览 (交互模式, 端口 {vmd_port})...")
            self.status_signal.emit("启动 VMD...")
            self.progress_signal.emit(100)

            cmd = f'"{vmd_exe}" -e "{script}"'
            vmd_proc = subprocess.Popen(cmd, shell=True, cwd=vmd_dir)
            self.log_signal.emit("VMD 已启动，请在 VMD 窗口中调整视角")
            self.log_signal.emit("调整好后点击 [渲染当前视角] 按钮出图")

            result = {
                'ok': True, 'action': 'preview',
                'vmd_port': vmd_port, 'vmd_proc': vmd_proc,
                'vmd_dir': vmd_dir, 'mode': mode,
            }
            self.finished_signal.emit(result)

        else:  # render
            if not output_path:
                output_path = os.path.splitext(input_files[0])[0] + "_esp.tga"
            self.log_signal.emit(f"无头渲染 -> {output_path} ...")
            self.status_signal.emit(f"渲染中: {os.path.basename(output_path)}")
            self.progress_signal.emit(98)

            script_dir = os.path.dirname(script)
            render_tcl = os.path.join(script_dir, "_esp_render.tcl")
            out_forward = output_path.replace("\\", "/")
            script_forward = script.replace("\\", "/")
            with open(render_tcl, "w", encoding="utf-8") as f:
                f.write(f"""source {script_forward}
render TachyonInternal {out_forward} rawaam TachyonInternal
quit
""")

            render_cmd = f'"{vmd_exe}" -dispdev text -e "{render_tcl}"'
            render_result = subprocess.run(render_cmd, shell=True, cwd=vmd_dir,
                                          capture_output=True, text=True, timeout=300)
            if render_result.returncode == 0:
                self.log_signal.emit(f"✓ 渲染完成: {output_path}")
                self.status_signal.emit(f"✓ 渲染完成: {os.path.basename(output_path)}")
                self.progress_signal.emit(100)
                result = {'ok': True, 'action': 'render', 'output_path': output_path}
            else:
                self.log_signal.emit("✗ 渲染失败")
                self.status_signal.emit("✗ 渲染失败")
                for line in render_result.stderr.strip().split("\n")[-15:]:
                    self.log_signal.emit(f"  | {line}")
                result = {'ok': False, 'action': 'render'}
            self.finished_signal.emit(result)

    @staticmethod
    def _apply_multiwfn_nthreads(multiwfn_exe, nthreads):
        """Write nthreads= setting to Multiwfn's settings.ini."""
        settings_path = os.path.join(os.path.dirname(multiwfn_exe), "settings.ini")
        if not os.path.isfile(settings_path):
            return
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                if line.strip().startswith("nthreads"):
                    new_lines.append(f"nthreads=  {nthreads}\n")
                else:
                    new_lines.append(line)
            with open(settings_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception:
            pass

    @staticmethod
    def _clean_vmd_dir(vmd_dir):
        """Clean up old visualization files in VMD directory."""
        if not os.path.isdir(vmd_dir):
            return
        patterns = ["density*.cub", "ESP*.cub", "mol*.pdb", "vtx*.pdb",
                    "surfanalysis.pdb", "esp_auto_*.vmd", "_esp_render.tcl"]
        for pattern in patterns:
            for f in glob.glob(os.path.join(vmd_dir, pattern)):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def _detect_esp_unit(self, pdb_path):
        """Read surfanalysis.pdb header to determine ESP unit."""
        try:
            with open(pdb_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("HEADER") or line.startswith("TITLE") or line.startswith("REMARK"):
                        if "eV" in line:
                            self.esp_unit = "eV"
                            self.log_signal.emit(f"  单位检测: eV")
                            return
                    if line.startswith("HETATM") or line.startswith("ATOM"):
                        break
            self.esp_unit = "kcal/mol"
            self.log_signal.emit(f"  单位检测: kcal/mol (默认)")
        except Exception:
            self.esp_unit = "kcal/mol"


class AreaAnalysisWorker(QThread):
    """Background thread for ESP area distribution analysis."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    busy_signal = pyqtSignal(bool)
    data_signal = pyqtSignal(list)  # emits [(fname, data_list), ...]

    def __init__(self, params):
        super().__init__()
        self.params = params
        self._abort_flag = False

    def abort(self):
        self._abort_flag = True

    def run(self):
        params = self.params
        input_files = params['input_files']
        multiwfn_exe = params['multiwfn_exe']
        work_dir = params['work_dir']
        range_low = params['range_low']
        range_high = params['range_high']
        n_bins = params['n_bins']

        self.busy_signal.emit(True)
        self.status_signal.emit("正在计算 ESP 分面面积分布...")
        self.log_signal.emit("")
        self.log_signal.emit("━━━ ESP 分区面积分布分析 ━━━")
        self.log_signal.emit(f"范围: {range_low} ~ {range_high} kcal/mol, 分区数: {n_bins}")

        all_data = []
        n_total = len(input_files)
        for idx, fch_path in enumerate(input_files):
            if self._abort_flag:
                self.log_signal.emit("[ABORT] 用户终止")
                break

            sys_num = idx + 1
            fch_name = os.path.basename(fch_path)
            self.log_signal.emit(f"\n分析文件 {sys_num}/{n_total}: {fch_name}")

            tmp_dir = os.path.join(work_dir, f"area_sys{sys_num}")
            os.makedirs(tmp_dir, exist_ok=True)
            fch_tmp = os.path.join(tmp_dir, f"sys{sys_num}.fch")
            shutil.copy2(fch_path, fch_tmp)

            def area_cb(line):
                stripped = line.rstrip()
                if len(stripped) > 3:
                    if len(stripped) > 70:
                        stripped = stripped[:67] + "..."
                    self.status_signal.emit(f"[面积分析 {sys_num}/{n_total}] {stripped}")
                pct, _ = _parse_progress(line)
                if pct is not None:
                    self.progress_signal.emit(int(((idx + pct) / n_total) * 100))

            ok, data = run_multiwfn_area_dist(
                multiwfn_exe, fch_tmp, tmp_dir,
                range_low=range_low, range_high=range_high,
                n_bins=n_bins, unit_code=3,
                progress_cb=area_cb, log_cb=self.log_signal.emit,
            )

            if data:
                all_data.append((fch_name, data))
                n_found = len(data)
                total_area = sum(d[1] for d in data)
                self.log_signal.emit(f"  已获取 {n_found} 个分区数据, 总表面积 = {total_area:.2f} Å²")
            else:
                if not ok:
                    self.log_signal.emit("  Multiwfn 返回异常且未解析到面积分布数据")
                else:
                    self.log_signal.emit("  Multiwfn 正常完成但未解析到面积分布数据")

        if not all_data:
            self.log_signal.emit("\n✗ 未获取到有效面积分布数据")
            self.busy_signal.emit(False)
            self.status_signal.emit("✗ 分析失败")
            self.finished_signal.emit(False)
            return

        # Save data files and emit data for interactive plotting
        out_dir = os.path.dirname(input_files[0])
        for fname, data in all_data:
            base = os.path.splitext(fname)[0]
            data_path = os.path.join(out_dir, f"{base}_esp_area_data.txt")
            saved = save_area_data_file(data, data_path)
            if saved:
                self.log_signal.emit(f"✓ 数据文件已保存: {saved}")

        self.data_signal.emit(all_data)
        self.progress_signal.emit(100)
        self.status_signal.emit("✓ ESP 分区面积图完成")
        self.busy_signal.emit(False)
        self.finished_signal.emit(True)


# ═══════════════════════════════════════════════════════════════════
#  Translation Dictionary
# ═══════════════════════════════════════════════════════════════════

TR = {
    "win_title": {"zh": "ESP Surface Visualization v2.0", "en": "ESP Surface Visualization v2.0"},
    "tab_analysis": {"zh": "🔬 分析与绘图", "en": "🔬 Analysis & Plot"},
    "tab_path": {"zh": "🔧 路径设置", "en": "🔧 Path Settings"},
    "lang_btn": {"zh": "EN", "en": "中"},
    "input_files": {"zh": "输入文件 (.fch / .fchk)", "en": "Input Files (.fch / .fchk)"},
    "path_settings": {"zh": "路径设置", "en": "Path Settings"},
    "mode_settings": {"zh": "分析模式与设置", "en": "Mode & Settings"},
    "run_log": {"zh": "运行日志", "en": "Run Log"},
    "add_file": {"zh": "添加文件", "en": "Add Files"},
    "add_dir": {"zh": "添加目录", "en": "Add Folder"},
    "clear_list": {"zh": "清空列表", "en": "Clear List"},
    "browse": {"zh": "浏览", "en": "Browse"},
    "save_cfg": {"zh": "保存路径配置", "en": "Save Config"},
    "detect_range": {"zh": "检测 ESP 范围", "en": "Detect ESP Range"},
    "mode": {"zh": "模式:", "en": "Mode:"},
    "color_low": {"zh": "色彩下限:", "en": "Low:"},
    "color_high": {"zh": "色彩上限:", "en": "High:"},
    "hint_unit": {"zh": "(单位: kcal/mol)", "en": "(Unit: kcal/mol)"},
    "pt_size": {"zh": "点大小(PT):", "en": "Point Size:"},
    "resolution": {"zh": "渲染分辨率:", "en": "Resolution:"},
    "opacity": {"zh": "透明度:", "en": "Opacity:"},
    "colorbar": {"zh": "显示色彩刻度轴", "en": "Show Color Scale Bar"},
    "show_labels": {"zh": "显示极值点", "en": "Show Extrema"},
    "nthreads": {"zh": "并行线程:", "en": "Threads:"},
    "pt_mode": {"zh": "PT 顶点着色", "en": "PT (Vertex Color)"},
    "iso_mode": {"zh": "ISO 等值面着色", "en": "ISO (Isosurface)"},
    "ext_mode": {"zh": "EXT 极值点", "en": "EXT (Extrema)"},
    "all_mode": {"zh": "ALL 全部叠加", "en": "ALL (Overlay)"},
    "btn_preview": {"zh": "▶ VMD 预览", "en": "▶ VMD Preview"},
    "btn_render_view": {"zh": "📷 渲染当前视角", "en": "📷 Render View"},
    "btn_pick": {"zh": "🔍 查询极值点", "en": "🔍 Query Extrema"},
    "btn_render": {"zh": "▼ 渲染输出", "en": "▼ Render Output"},
    "btn_area": {"zh": "📊 ESP分区面积图", "en": "📊 ESP Area Chart"},
    "btn_stop": {"zh": "■ 停止", "en": "■ Stop"},
}

# ═══════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════

class ESPSurfaceGUI(QMainWindow):
    pick_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        self.config = load_config()
        self.input_files = []
        self.work_dir = tempfile.mkdtemp(prefix="esp_gui_")
        self.vmd_process = None
        self.vmd_port = None
        self.vmd_render_dir = None
        self.callback_port = None
        self.callback_server = None
        self.callback_thread = None
        self.esp_unit = "kcal/mol"

        self._worker = None
        self._vmd_persist_sock = None
        self.pick_signal.connect(self._handle_pick)
        self._lang = "zh"

        self._init_ui()
        self._apply_lang_ui()
        self._log("ESP Surface Visualization GUI v2.0 (PyQt5) 已启动")
        self._log(f"工作目录: {self.work_dir}")

        self.setWindowTitle("ESP Surface Visualization v2.0")
        self.setMinimumSize(950, 600)
        self.resize(950, 1100)

    def closeEvent(self, event):
        self._close_persist_sock()
        self._stop_callback_server()
        if self.vmd_process:
            try:
                self.vmd_process.terminate()
                self.vmd_process.wait(timeout=5)
            except Exception:
                pass
        self._cleanup_work_dir()
        super().closeEvent(event)

    def _cleanup_work_dir(self):
        if os.path.exists(self.work_dir):
            try:
                shutil.rmtree(self.work_dir)
                self._log(f"已清理临时目录: {self.work_dir}")
            except Exception as e:
                self._log(f"清理临时目录失败: {e}")

    # ══ UI Construction ══

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Main Panel (Tabbed) ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.left_tabs = QTabWidget()

        # Tab 1: Analysis & Run
        tab_analysis = QWidget()
        tab_analysis_layout = QVBoxLayout(tab_analysis)
        tab_analysis_layout.setContentsMargins(4, 4, 4, 4)
        tab_analysis_layout.setSpacing(6)
        self._build_input_files(tab_analysis_layout)
        self._build_mode_settings(tab_analysis_layout)
        self._build_control_bar(tab_analysis_layout)
        self._build_progress(tab_analysis_layout)
        self._build_log(tab_analysis_layout)
        self.left_tabs.addTab(tab_analysis, "🔬 分析与绘图")

        # Tab 2: Path Settings
        tab_path = QWidget()
        tab_path_layout = QVBoxLayout(tab_path)
        tab_path_layout.setContentsMargins(4, 4, 4, 4)
        tab_path_layout.setSpacing(6)
        self._build_path_settings(tab_path_layout)
        tab_path_layout.addStretch()
        self.left_tabs.addTab(tab_path, "🔧 路径设置")

        left_layout.addWidget(self.left_tabs)
        root.addWidget(left, stretch=1)

    def _build_path_settings(self, parent_layout):
        self._path_settings_gb = QGroupBox("路径设置")
        gb = self._path_settings_gb
        gl = QGridLayout()
        gl.setVerticalSpacing(4)
        gl.setHorizontalSpacing(6)

        # Multiwfn
        gl.addWidget(QLabel("Multiwfn:"), 0, 0)
        self.mwfn_edit = QLineEdit(self.config["multiwfn"])
        self.mwfn_edit.setPlaceholderText("Multiwfn.exe 路径")
        gl.addWidget(self.mwfn_edit, 0, 1)
        btn_mwfn = QPushButton("浏览")
        btn_mwfn.setCursor(Qt.PointingHandCursor)
        btn_mwfn.clicked.connect(lambda: self._browse_exe(self.mwfn_edit, "Multiwfn.exe"))
        gl.addWidget(btn_mwfn, 0, 2)

        # VMD
        gl.addWidget(QLabel("VMD exe:"), 1, 0)
        self.vmd_edit = QLineEdit(self.config["vmd"])
        self.vmd_edit.setPlaceholderText("vmd.exe 路径")
        gl.addWidget(self.vmd_edit, 1, 1)
        btn_vmd = QPushButton("浏览")
        btn_vmd.setCursor(Qt.PointingHandCursor)
        btn_vmd.clicked.connect(lambda: self._browse_exe(self.vmd_edit, "vmd.exe"))
        gl.addWidget(btn_vmd, 1, 2)

        # VMD Dir
        gl.addWidget(QLabel("VMD 目录:"), 2, 0)
        self.vmdir_edit = QLineEdit(self.config.get("vmd_dir", ""))
        self.vmdir_edit.setPlaceholderText("VMD 工作目录")
        gl.addWidget(self.vmdir_edit, 2, 1)
        btn_vmdir = QPushButton("浏览")
        btn_vmdir.setCursor(Qt.PointingHandCursor)
        btn_vmdir.clicked.connect(lambda: self._browse_dir(self.vmdir_edit, "选择 VMD 工作目录"))
        gl.addWidget(btn_vmdir, 2, 2)

        # Tachyon
        gl.addWidget(QLabel("Tachyon:"), 3, 0)
        self.tachyon_edit = QLineEdit(self.config.get("tachyon", DEFAULT_TACHYON))
        self.tachyon_edit.setPlaceholderText("tachyon_WIN32.exe 路径")
        gl.addWidget(self.tachyon_edit, 3, 1)
        btn_tachyon = QPushButton("浏览")
        btn_tachyon.setCursor(Qt.PointingHandCursor)
        btn_tachyon.clicked.connect(lambda: self._browse_exe(self.tachyon_edit, "tachyon_WIN32.exe"))
        gl.addWidget(btn_tachyon, 3, 2)

        # Save config
        self._btn_save_cfg = QPushButton("保存路径配置")
        self._btn_save_cfg.setCursor(Qt.PointingHandCursor)
        self._btn_save_cfg.setStyleSheet(
            "QPushButton { background: #43A047; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 6px; border: none; }"
            "QPushButton:hover { background: #388E3C; }"
        )
        self._btn_save_cfg.clicked.connect(self._save_paths)
        gl.addWidget(self._btn_save_cfg, 0, 3, 4, 1)

        gl.setColumnStretch(1, 1)
        # Right-align labels
        for i in range(gl.count()):
            w = gl.itemAt(i).widget()
            if isinstance(w, QLabel):
                w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        gb.setLayout(gl)
        parent_layout.addWidget(gb)

    def _build_input_files(self, parent_layout):
        self._input_files_gb = QGroupBox("输入文件 (.fch / .fchk)")
        gb = self._input_files_gb
        vl = QVBoxLayout()

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("添加文件")
        self._btn_add.setCursor(Qt.PointingHandCursor)
        self._btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(self._btn_add)

        self._btn_adddir = QPushButton("添加目录")
        self._btn_adddir.setCursor(Qt.PointingHandCursor)
        self._btn_adddir.clicked.connect(self._add_dir)
        btn_row.addWidget(self._btn_adddir)

        self._btn_clear_files = QPushButton("清空列表")
        self._btn_clear_files.setCursor(Qt.PointingHandCursor)
        self._btn_clear_files.clicked.connect(self._clear_files)
        btn_row.addWidget(self._btn_clear_files)

        self.lbl_filecount = QLabel("(0 个文件)")
        btn_row.addWidget(self.lbl_filecount)

        btn_row.addStretch()

        self._lang_btn = QPushButton("EN")
        self._lang_btn.setCursor(Qt.PointingHandCursor)
        self._lang_btn.setFixedSize(60, 28)
        self._lang_btn.setStyleSheet(
            "QPushButton { background: #5C6BC0; color: white; font-weight: bold; "
            "font-size: 9pt; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #3F51B5; }"
        )
        self._lang_btn.clicked.connect(self._switch_lang)
        btn_row.addWidget(self._lang_btn)

        vl.addLayout(btn_row)

        self.file_list = QListWidget()
        self.file_list.setStyleSheet(
            "QListWidget { background: #F8FAFC; color: #1E293B; border: 1px solid #CBD5E1; "
            "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 9pt; }"
        )
        self.file_list.setMaximumHeight(100)
        vl.addWidget(self.file_list)
        gb.setLayout(vl)
        parent_layout.addWidget(gb)

    def _build_mode_settings(self, parent_layout):
        self._mode_settings_gb = QGroupBox("分析模式与设置")
        gb = self._mode_settings_gb
        gl = QGridLayout()
        gl.setVerticalSpacing(4)
        gl.setHorizontalSpacing(6)

        # Mode radio buttons
        self._lbl_mode = QLabel("模式:")
        gl.addWidget(self._lbl_mode, 0, 0)
        self.mode_group = QButtonGroup(self)
        modes = [
            ("PT 顶点着色", "pt"),
            ("ISO 等值面着色", "iso"),
            ("EXT 极值点", "ext"),
            ("ALL 全部叠加", "all"),
        ]
        self._rb_pt = self._rb_iso = self._rb_ext = self._rb_all = None
        for i, (txt, val) in enumerate(modes):
            rb = QRadioButton(txt)
            rb.setStyleSheet("QRadioButton::indicator { border-radius: 7px; }")
            if i == 0:
                rb.setChecked(True)
                self._rb_pt = rb
            elif i == 1:
                self._rb_iso = rb
            elif i == 2:
                self._rb_ext = rb
            else:
                self._rb_all = rb
            self.mode_group.addButton(rb, i)
            gl.addWidget(rb, 0, 1 + i)

        # Color scale
        self._lbl_clow = QLabel("色彩下限:")
        gl.addWidget(self._lbl_clow, 1, 0)
        self.clow_edit = QLineEdit("-50")
        self.clow_edit.setMaximumWidth(80)
        gl.addWidget(self.clow_edit, 1, 1)

        self._lbl_chigh = QLabel("色彩上限:")
        gl.addWidget(self._lbl_chigh, 1, 2)
        self.chigh_edit = QLineEdit("50")
        self.chigh_edit.setMaximumWidth(80)
        gl.addWidget(self.chigh_edit, 1, 3)

        self._hint_lbl = QLabel("(单位: kcal/mol)")
        self._hint_lbl.setObjectName("HintLabel")
        gl.addWidget(self._hint_lbl, 1, 4, 1, 2)

        self.clow_edit.editingFinished.connect(self._on_color_range_changed)
        self.chigh_edit.editingFinished.connect(self._on_color_range_changed)

        self._btn_detect_range = QPushButton("检测 ESP 范围")
        self._btn_detect_range.setCursor(Qt.PointingHandCursor)
        self._btn_detect_range.setStyleSheet(
            "QPushButton { background: #00796B; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 5px; border: none; font-size: 9pt; }"
            "QPushButton:hover { background: #00695C; }"
        )
        self._btn_detect_range.clicked.connect(self._action_detect_range)
        gl.addWidget(self._btn_detect_range, 1, 6, 1, 2)

        # Point size + Resolution + Thread count (same row)
        self._lbl_ptsize = QLabel("点大小(PT):")
        gl.addWidget(self._lbl_ptsize, 2, 0)
        self.ptsize_edit = QLineEdit("2.0")
        self.ptsize_edit.setMaximumWidth(80)
        gl.addWidget(self.ptsize_edit, 2, 1)

        self._lbl_res = QLabel("渲染分辨率:")
        gl.addWidget(self._lbl_res, 2, 2)
        self.res_combo = QComboBox()
        self.res_combo.addItems(["2000x1500", "1200x900", "3000x2250", "4000x3000"])
        self.res_combo.setCurrentText("2000x1500")
        self.res_combo.setMaximumWidth(150)
        gl.addWidget(self.res_combo, 2, 3)

        self._lbl_nthreads = QLabel("并行线程:")
        gl.addWidget(self._lbl_nthreads, 2, 4)
        self.nthreads_spin = QSpinBox()
        self.nthreads_spin.setRange(1, 64)
        self.nthreads_spin.setValue(14)
        self.nthreads_spin.setMaximumWidth(80)
        self._lbl_nthreads.setBuddy(self.nthreads_spin)
        gl.addWidget(self.nthreads_spin, 2, 5)
        nthreads_hint = QLabel("按自己电脑情况设置")
        nthreads_hint.setObjectName("HintLabel")
        gl.addWidget(nthreads_hint, 2, 6, 1, 2)

        # Colorbar + extremum label controls
        self.colorbar_cb = QCheckBox("显示色彩刻度轴")
        self.colorbar_cb.setChecked(False)
        self.colorbar_cb.stateChanged.connect(self._on_colorbar_toggle)
        gl.addWidget(self.colorbar_cb, 3, 1)

        self._show_labels_cb = QCheckBox("显示极值数值")
        self._show_labels_cb.setChecked(False)
        self._show_labels_cb.stateChanged.connect(self._on_show_labels_toggle)
        gl.addWidget(self._show_labels_cb, 3, 2)

        gl.addWidget(QLabel("字号:"), 3, 3)
        self._label_size_spin = QDoubleSpinBox()
        self._label_size_spin.setRange(1.0, 5.0)
        self._label_size_spin.setSingleStep(0.1)
        self._label_size_spin.setValue(1.5)
        self._label_size_spin.setDecimals(1)
        self._label_size_spin.setMaximumWidth(55)
        self._label_size_spin.valueChanged.connect(self._on_label_size_changed)
        gl.addWidget(self._label_size_spin, 3, 4)

        gl.addWidget(QLabel("偏移:"), 3, 5)
        self._label_offset_spin = QDoubleSpinBox()
        self._label_offset_spin.setRange(0.2, 3.0)
        self._label_offset_spin.setSingleStep(0.1)
        self._label_offset_spin.setValue(0.8)
        self._label_offset_spin.setDecimals(1)
        self._label_offset_spin.setMaximumWidth(55)
        self._label_offset_spin.valueChanged.connect(self._on_label_offset_changed)
        gl.addWidget(self._label_offset_spin, 3, 6)

        # Opacity slider
        self._lbl_opacity = QLabel("透明度:")
        gl.addWidget(self._lbl_opacity, 4, 0)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(70)
        self.opacity_slider.setEnabled(False)
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        gl.addWidget(self.opacity_slider, 4, 1, 1, 3)
        self.opacity_value_label = QLabel("0.70")
        self.opacity_value_label.setMinimumWidth(40)
        self.opacity_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        gl.addWidget(self.opacity_value_label, 4, 4)

        # Mode change logic
        self.mode_group.buttonClicked.connect(self._on_mode_change)

        gl.setColumnStretch(1, 1)
        gl.setColumnStretch(3, 1)
        for i in range(gl.count()):
            w = gl.itemAt(i).widget()
            if isinstance(w, QLabel):
                w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        gb.setLayout(gl)
        parent_layout.addWidget(gb)

    def _build_control_bar(self, parent_layout):
        ctrl = QHBoxLayout()

        self.btn_preview = QPushButton("▶ VMD 预览")
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.setStyleSheet(
            "QPushButton { background: #43A047; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 22px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #388E3C; }"
            "QPushButton:disabled { background: #BDBDBD; color: #E0E0E0; }"
        )
        self.btn_preview.clicked.connect(self._action_preview)
        ctrl.addWidget(self.btn_preview)

        self.btn_render_view = QPushButton("📷 渲染当前视角")
        self.btn_render_view.setCursor(Qt.PointingHandCursor)
        self.btn_render_view.setStyleSheet(
            "QPushButton { background: #FF9800; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 18px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #F57C00; }"
            "QPushButton:disabled { background: #BDBDBD; color: #E0E0E0; }"
        )
        self.btn_render_view.setEnabled(False)
        self.btn_render_view.clicked.connect(self._action_render_view)
        ctrl.addWidget(self.btn_render_view)

        self.btn_pick = QPushButton("🔍 查询极值点")
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.setStyleSheet(
            "QPushButton { background: #9C27B0; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 18px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #7B1FA2; }"
            "QPushButton:disabled { background: #BDBDBD; color: #E0E0E0; }"
        )
        self.btn_pick.setEnabled(False)
        self.btn_pick.clicked.connect(self._action_pick)
        ctrl.addWidget(self.btn_pick)

        self.btn_render_out = QPushButton("▼ 渲染输出")
        self.btn_render_out.setCursor(Qt.PointingHandCursor)
        self.btn_render_out.setStyleSheet(
            "QPushButton { background: #1E88E5; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 22px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #1976D2; }"
            "QPushButton:disabled { background: #BDBDBD; color: #E0E0E0; }"
        )
        self.btn_render_out.clicked.connect(self._action_render)
        ctrl.addWidget(self.btn_render_out)
        self.btn_render_out.hide()

        self.btn_area_chart = QPushButton("📊 ESP分区面积图")
        self.btn_area_chart.setCursor(Qt.PointingHandCursor)
        self.btn_area_chart.setStyleSheet(
            "QPushButton { background: #795548; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 18px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #5D4037; }"
            "QPushButton:disabled { background: #BDBDBD; color: #E0E0E0; }"
        )
        self.btn_area_chart.clicked.connect(self._action_area_chart)
        ctrl.addWidget(self.btn_area_chart)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setStyleSheet(
            "QPushButton { background: #F44336; color: white; font-weight: bold; "
            "font-size: 11pt; padding: 8px 22px; border-radius: 16px; border: none; }"
            "QPushButton:hover { background: #D32F2F; }"
        )
        self.btn_stop.clicked.connect(self._action_stop)
        ctrl.addWidget(self.btn_stop)

        ctrl.addStretch()
        parent_layout.addLayout(ctrl)

    def _build_progress(self, parent_layout):
        prog_fr = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 6px; background: #E2E8F0; height: 16px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #42A5F5, stop:1 #1565C0); border-radius: 6px; }"
        )
        prog_fr.addWidget(self.progress_bar)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #64748B; font-size: 9pt; font-family: 'Consolas', monospace;")
        prog_fr.addWidget(self.lbl_status)
        parent_layout.addLayout(prog_fr)

    def _build_log(self, parent_layout):
        self._log_gb = QGroupBox("运行日志")
        gb = self._log_gb
        vl = QVBoxLayout()
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet(
            "QTextEdit { background: #F1F5F9; color: #1E293B; border: 1px solid #CBD5E1; "
            "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace; "
            "font-size: 9pt; }"
        )
        vl.addWidget(self.log_edit)
        gb.setLayout(vl)
        parent_layout.addWidget(gb, stretch=1)

    # ══ UI Callbacks ══

    def _log(self, msg):
        self.log_edit.append(msg)
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_progress(self, value):
        self.progress_bar.setValue(max(0, min(100, value)))

    def _set_status(self, msg):
        self.lbl_status.setText(msg)

    def _set_busy(self, busy=True):
        state = "normal" if not busy else "disabled"
        for btn in (self.btn_preview, self.btn_render_out, self.btn_area_chart):
            btn.setEnabled(not busy)
        if busy:
            self.btn_render_view.setEnabled(False)
            self.btn_pick.setEnabled(False)
            self.opacity_slider.setEnabled(False)

    # ══ i18n ══

    def _tr(self, key, **fmt):
        s = TR.get(key, {}).get(self._lang, key)
        return s.format(**fmt) if fmt else s

    def _switch_lang(self):
        self._lang = "en" if self._lang == "zh" else "zh"
        self._apply_lang_ui()

    def _apply_lang_ui(self):
        self.setWindowTitle(self._tr("win_title"))
        self.left_tabs.setTabText(0, self._tr("tab_analysis"))
        self.left_tabs.setTabText(1, self._tr("tab_path"))
        self._lang_btn.setText(self._tr("lang_btn"))
        self._input_files_gb.setTitle(self._tr("input_files"))
        self._path_settings_gb.setTitle(self._tr("path_settings"))
        self._mode_settings_gb.setTitle(self._tr("mode_settings"))
        self._log_gb.setTitle(self._tr("run_log"))
        self._btn_add.setText(self._tr("add_file"))
        self._btn_adddir.setText(self._tr("add_dir"))
        self._btn_clear_files.setText(self._tr("clear_list"))
        self._btn_save_cfg.setText(self._tr("save_cfg"))
        self._btn_detect_range.setText(self._tr("detect_range"))
        self._lbl_mode.setText(self._tr("mode"))
        self._lbl_clow.setText(self._tr("color_low"))
        self._lbl_chigh.setText(self._tr("color_high"))
        self._hint_lbl.setText(self._tr("hint_unit"))
        self._lbl_ptsize.setText(self._tr("pt_size"))
        self._lbl_res.setText(self._tr("resolution"))
        self._lbl_opacity.setText(self._tr("opacity"))
        self._lbl_nthreads.setText(self._tr("nthreads"))
        self.colorbar_cb.setText(self._tr("colorbar"))
        self._show_labels_cb.setText(self._tr("show_labels"))
        self._rb_pt.setText(self._tr("pt_mode"))
        self._rb_iso.setText(self._tr("iso_mode"))
        self._rb_ext.setText(self._tr("ext_mode"))
        self._rb_all.setText(self._tr("all_mode"))
        self.btn_preview.setText(self._tr("btn_preview"))
        self.btn_render_view.setText(self._tr("btn_render_view"))
        self.btn_pick.setText(self._tr("btn_pick"))
        self.btn_render_out.setText(self._tr("btn_render"))
        self.btn_area_chart.setText(self._tr("btn_area"))
        self.btn_stop.setText(self._tr("btn_stop"))

    def _browse_exe(self, edit, name):
        p, _ = QFileDialog.getOpenFileName(self, f"选择 {name}", "", "EXE (*.exe);;All (*)")
        if p:
            edit.setText(p)

    def _browse_dir(self, edit, title):
        d = QFileDialog.getExistingDirectory(self, title)
        if d:
            edit.setText(d)

    def _save_paths(self):
        save_config(
            self.mwfn_edit.text(), self.vmd_edit.text(),
            self.vmdir_edit.text(), self.tachyon_edit.text()
        )
        self._log("路径配置已保存")

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 .fch / .fchk 文件", "",
            "FCH/FCHK (*.fch *.fchk);;All (*)"
        )
        for f in files:
            if f not in self.input_files:
                self.input_files.append(f)
                self.file_list.addItem(f)
        self.lbl_filecount.setText(f"({len(self.input_files)} 个文件)")

    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 .fch 文件的目录")
        if not d:
            return
        for ext in ("*.fch", "*.fchk"):
            for f in glob.glob(os.path.join(d, ext)):
                if f not in self.input_files:
                    self.input_files.append(f)
                    self.file_list.addItem(f)
        self.lbl_filecount.setText(f"({len(self.input_files)} 个文件)")

    def _clear_files(self):
        self.input_files.clear()
        self.file_list.clear()
        self.lbl_filecount.setText("(0 个文件)")

    def _get_mode(self):
        btn = self.mode_group.checkedButton()
        if btn is None:
            return "pt"
        text = btn.text()
        if "PT" in text:
            return "pt"
        elif "ISO" in text:
            return "iso"
        elif "EXT" in text:
            return "ext"
        elif "ALL" in text:
            return "all"
        return "pt"

    def _on_mode_change(self, btn):
        text = btn.text()
        if "PT" in text:
            self.clow_edit.setText("-50")
            self.chigh_edit.setText("50")
        elif "ISO" in text:
            self.clow_edit.setText("-22")
            self.chigh_edit.setText("22")
        elif "EXT" in text:
            self.clow_edit.setText("-50")
            self.chigh_edit.setText("50")
        elif "ALL" in text:
            self.clow_edit.setText("-22")
            self.chigh_edit.setText("22")

    def _validate_inputs(self):
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请先添加输入文件")
            return False
        multiwfn_exe = self.mwfn_edit.text()
        vmd_exe = self.vmd_edit.text()
        if not os.path.isfile(multiwfn_exe):
            QMessageBox.critical(self, "错误", f"Multiwfn 不存在:\n{multiwfn_exe}")
            return False
        if not os.path.isfile(vmd_exe):
            QMessageBox.critical(self, "错误", f"VMD 不存在:\n{vmd_exe}")
            return False
        try:
            float(self.clow_edit.text())
            float(self.chigh_edit.text())
            float(self.ptsize_edit.text())
        except ValueError:
            QMessageBox.critical(self, "错误", "色彩范围或点大小不是有效数字")
            return False
        return True

    def _get_params(self, action, output_path=None):
        mode = self._get_mode()
        vmd_dir = self.vmdir_edit.text() or os.path.dirname(self.vmd_edit.text())
        return {
            'action': action,
            'mode': mode,
            'input_files': list(self.input_files),
            'multiwfn_exe': self.mwfn_edit.text(),
            'vmd_exe': self.vmd_edit.text(),
            'vmd_dir': vmd_dir,
            'color_low': float(self.clow_edit.text()),
            'color_high': float(self.chigh_edit.text()),
            'pt_size': float(self.ptsize_edit.text()),
            'show_colorbar': self.colorbar_cb.isChecked(),
            'output_path': output_path or None,
            'work_dir': self.work_dir,
            'callback_port': self.callback_port or 0,
            'nthreads': self.nthreads_spin.value(),
        }

    # ══ Actions ══

    def _action_preview(self):
        if not self._validate_inputs():
            return
        self._log("")
        self._log("━━━ VMD 预览 ━━━")
        self._start_callback_server()
        self._run_job("preview")

    def _action_render(self):
        if not self._validate_inputs():
            return
        default_name = os.path.splitext(self.input_files[0])[0] + "_esp.tga"
        output_path, _ = QFileDialog.getSaveFileName(
            self, "保存渲染输出", default_name,
            "TGA (*.tga);;PNG (*.png);;BMP (*.bmp)"
        )
        if not output_path:
            return
        self._log("")
        self._log("━━━ 渲染输出 ━━━")
        self._run_job("render", output_path=output_path)

    def _action_render_view(self):
        if not self.vmd_port:
            QMessageBox.warning(self, "提示", "请先点击 [VMD 预览] 启动 VMD")
            return
        tachyon_exe = self.tachyon_edit.text().strip()
        if not os.path.isfile(tachyon_exe):
            QMessageBox.critical(self, "错误", f"Tachyon 不存在:\n{tachyon_exe}")
            return

        # Pop up save dialog
        default_name = ""
        if self.input_files:
            default_name = os.path.splitext(self.input_files[0])[0] + "_esp.png"
        output_path, _ = QFileDialog.getSaveFileName(
            self, "保存渲染输出", default_name,
            "PNG (*.png);;TGA (*.tga);;BMP (*.bmp)"
        )
        if not output_path:
            return

        try:
            w_s, h_s = self.res_combo.currentText().split("x")
            resolution = (int(w_s), int(h_s))
        except (ValueError, AttributeError):
            resolution = (2000, 1500)

        self.btn_render_view.setEnabled(False)

        nthreads = self.nthreads_spin.value()

        class RenderViewWorker(QThread):
            log_signal = pyqtSignal(str)
            finished_signal = pyqtSignal(bool, str)

            def __init__(self, vmd_port, vmd_dir, tachyon_exe, output_path, resolution, nthreads):
                super().__init__()
                self.vmd_port = vmd_port
                self.vmd_dir = vmd_dir
                self.tachyon_exe = tachyon_exe
                self.output_path = output_path
                self.resolution = resolution
                self.nthreads = nthreads

            def run(self):
                try:
                    self.log_signal.emit(
                        f"\n正在渲染当前视角 ({self.resolution[0]}x{self.resolution[1]})..."
                    )
                    t0 = time.time()
                    result = render_current_view_tachyon(
                        self.vmd_port, self.vmd_dir,
                        self.tachyon_exe, self.output_path, resolution=self.resolution,
                        nthreads=self.nthreads
                    )
                    dt = time.time() - t0
                    if result:
                        self.log_signal.emit(f"✓ 渲染完成 ({dt:.1f}s): {result}")
                        self.finished_signal.emit(True, result)
                    else:
                        self.log_signal.emit(f"✗ 渲染失败 ({dt:.1f}s)")
                        self.finished_signal.emit(False, "Tachyon 渲染失败，请检查日志")
                except Exception as e:
                    self.log_signal.emit(f"✗ 渲染错误: {e}")
                    self.finished_signal.emit(False, str(e))

        self._render_view_worker = RenderViewWorker(
            self.vmd_port, self.vmd_render_dir,
            tachyon_exe, output_path, resolution,
            nthreads
        )
        self._render_view_worker.log_signal.connect(self._log)

        def on_render_view_done(ok, msg):
            self.btn_render_view.setEnabled(True)
            if ok:
                try:
                    os.startfile(msg)
                except Exception:
                    pass
            else:
                QMessageBox.critical(self, "渲染失败", msg)
        self._render_view_worker.finished_signal.connect(on_render_view_done)
        self._render_view_worker.start()

    def _action_pick(self):
        if not self.vmd_port:
            QMessageBox.warning(self, "提示", "请先点击 [VMD 预览] 启动 VMD")
            return
        resp = send_vmd_cmd(self.vmd_port, "mouse mode 4", timeout=3)
        if "ERROR" in resp:
            self._log(f"⚠ VMD 进入选取模式失败: {resp}")
            self._set_pick_mode_ui(False)
        else:
            self._log("")
            self._log("━━━ 查询极值点 ━━━")
            self._log("VMD 已进入选取模式，请点击极值点球查询 ESP 值")
            self._log("（C = 极大值点, O = 极小值点）")
            self._log("提示：请点击 surfanalysis.pdb 中的极值点球，不是分子骨架原子")
            self._set_status("选取模式：点击 VMD 中的极值点球（C/O 原子）")
            self._set_pick_mode_ui(True)

    def _set_pick_mode_ui(self, active):
        if active:
            self.btn_pick.setText("● 选取中...")
        else:
            self.btn_pick.setText("🔍 查询极值点")

    def _on_show_labels_toggle(self, state):
        """Toggle ESP value labels AND extremum point spheres in VMD."""
        if not self.vmd_port:
            return
        if state:
            size = self._label_size_spin.value()
            offset = self._label_offset_spin.value()
            self._send_vmd_cmd_fast("show_extrema_points")
            self._send_vmd_cmd_fast(f"show_extrema_labels {size} {offset}")
            self._log(f"极值点已显示 (字号: {size}, 偏移: {offset}, 红=极大, 蓝=极小)")
        else:
            self._send_vmd_cmd_fast("clear_extrema_labels")
            self._send_vmd_cmd_fast("hide_extrema_points")
            self._log("极值点已清除")

    def _on_label_size_changed(self, val):
        """When font size changes, redraw labels if currently shown."""
        if not self.vmd_port or not self._show_labels_cb.isChecked():
            return
        offset = self._label_offset_spin.value()
        self._send_vmd_cmd_fast(f"show_extrema_labels {val} {offset}")

    def _on_label_offset_changed(self, val):
        """When offset changes, redraw labels if currently shown."""
        if not self.vmd_port or not self._show_labels_cb.isChecked():
            return
        size = self._label_size_spin.value()
        self._send_vmd_cmd_fast(f"show_extrema_labels {size} {val}")

    def _action_detect_range(self):
        """Run Multiwfn module 12→0 standalone to query ESP surface min/max."""
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请先添加输入文件")
            return

        multiwfn_exe = self.mwfn_edit.text()
        if not os.path.isfile(multiwfn_exe):
            QMessageBox.critical(self, "错误", f"Multiwfn 不存在:\n{multiwfn_exe}")
            return

        mode = self._get_mode()
        fch_path = self.input_files[0]

        tmp_dir = os.path.join(self.work_dir, "_range_query")
        os.makedirs(tmp_dir, exist_ok=True)
        fch_tmp = os.path.join(tmp_dir, "tmp.fch")
        try:
            shutil.copy2(fch_path, fch_tmp)
        except Exception as e:
            self._log(f"文件复制失败: {e}")
            return

        self._log("━━━ 查询 ESP 范围 ━━━")
        self._log(f"文件: {os.path.basename(fch_path)}")
        self._set_status("正在查询 ESP 范围...")

        ok, stdout = run_multiwfn(multiwfn_exe, fch_tmp, CMD_ESP_QUERY_RANGE, tmp_dir)
        if not ok:
            self._log("✗ Multiwfn 运行失败")
            self._set_status("查询失败")
            return

        result = _parse_esp_range_from_stdout(stdout)
        if not result:
            self._log("✗ 无法从输出中解析 ESP 范围")
            self._set_status("解析失败")
            return

        low_val, high_val, unit = result
        if mode in ("iso", "all"):
            if unit == 'a.u.':
                lo_kcal = low_val * AU
                hi_kcal = high_val * AU
                self._log(f"        (原始: {low_val:.6f} ~ {high_val:.6f} a.u.)")
            else:
                lo_kcal = low_val
                hi_kcal = high_val
            self.clow_edit.setText(f"{lo_kcal:.2f}")
            self.chigh_edit.setText(f"{hi_kcal:.2f}")
            self._log(f"ESP 范围: {lo_kcal:.2f} ~ {hi_kcal:.2f} kcal/mol")
        else:
            self.clow_edit.setText(f"{low_val:.2f}")
            self.chigh_edit.setText(f"{high_val:.2f}")
            self._log(f"ESP 范围 (kcal/mol): {low_val:.2f} ~ {high_val:.2f}")

        self._set_status("ESP 范围已填充 (kcal/mol)")
        self._on_color_range_changed()

    def _on_color_range_changed(self):
        """用户修改色彩上下限后，实时推送 mol scaleminmax 到已运行的 VMD。
        Text fields are always in kcal/mol; for ISO/ALL mode, convert to a.u. for VMD.
        """
        if not self.vmd_port:
            return
        try:
            low = float(self.clow_edit.text())
            high = float(self.chigh_edit.text())
        except ValueError:
            return
        mode = self._get_mode()
        nsystems = max(1, len(self.input_files))
        self._log(f"色彩范围实时更新: {low} ~ {high}")
        if mode in ("iso", "all"):
            vmd_low = low / AU
            vmd_high = high / AU
            for mid in range(nsystems):
                self._send_vmd_cmd_fast(f"mol scaleminmax {mid} 1 {vmd_low} {vmd_high}")
        elif mode == "pt":
            for i in range(1, nsystems * 2, 2):
                self._send_vmd_cmd_fast(f"mol scaleminmax {i} 0 {low} {high}")
        # Rebuild colorbar if visible
        if self.colorbar_cb.isChecked():
            tcl = (
                "catch {package require colorscalebar}; "
                "catch {::ColorScaleBar::delete_color_scale_bar}; "
                f'::ColorScaleBar::color_scale_bar 1.5 0.08 0 1 {low} {high} 10 16 0 0.82 -0.75 1 top 0 1 "ESP (kcal/mol)"'
            )
            self._send_vmd_cmd_fast(tcl)

    def _on_opacity_slider_changed(self, val):
        op = val / 100.0
        self.opacity_value_label.setText(f"{op:.2f}")
        self._send_vmd_cmd_fast(f"material change opacity EdgyGlass {op}")

    def _send_vmd_cmd_fast(self, cmd):
        """通过持久化 TCP socket 向 VMD 发送一条 Tcl 命令。

        recv 返回空字节表示 VMD 关闭了本连接，此时只清理 socket 不重试
        （命令已发出，VMD 端 fileevent 会处理），避免重复发送。
        下次调用时 self._vmd_persist_sock 为 None 会自动重建连接。
        """
        if not self.vmd_port:
            return
        try:
            if self._vmd_persist_sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(("127.0.0.1", self.vmd_port))
                self._vmd_persist_sock = sock
            self._vmd_persist_sock.sendall((cmd + "\n").encode("utf-8"))
            try:
                self._vmd_persist_sock.settimeout(0.5)
                while True:
                    chunk = self._vmd_persist_sock.recv(4096)
                    if not chunk:
                        # VMD 关闭了连接，清理 socket，下次调用会重连。
                        # 命令已经发出，无需重发。
                        self._close_persist_sock()
                        return
                    if b"\n" in chunk:
                        break
            except socket.timeout:
                pass
            self._vmd_persist_sock.settimeout(5)
        except Exception:
            self._close_persist_sock()

    def _close_persist_sock(self):
        """关闭 VMD 持久化 socket 连接。"""
        if self._vmd_persist_sock:
            try:
                self._vmd_persist_sock.close()
            except Exception:
                pass
            self._vmd_persist_sock = None

    def _on_colorbar_toggle(self, state):
        """Dynamically show/hide the VMD color scale bar."""
        if not self.vmd_port:
            return
        if state:
            try:
                lo = float(self.clow_edit.text())
                hi = float(self.chigh_edit.text())
            except ValueError:
                lo, hi = -22, 22
            tcl = (
                "catch {package require colorscalebar}; "
                "catch {::ColorScaleBar::delete_color_scale_bar}; "
                f'::ColorScaleBar::color_scale_bar 1.5 0.08 0 1 {lo} {hi} 10 16 0 0.82 -0.75 1 top 0 1 "ESP (kcal/mol)"'
            )
            self._send_vmd_cmd_fast(tcl)
            self._log(f"色彩刻度轴已显示 ({lo} ~ {hi} kcal/mol)")
        else:
            self._send_vmd_cmd_fast("catch {::ColorScaleBar::delete_color_scale_bar}")
            self._log("色彩刻度轴已隐藏")

    def _action_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
        self._close_persist_sock()
        os.system("taskkill /F /IM vmd.exe 2>nul")
        self.opacity_slider.setEnabled(False)
        self._log("已发送停止信号")

    def _action_area_chart(self):
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请先添加输入文件")
            return
        multiwfn_exe = self.mwfn_edit.text()
        if not os.path.isfile(multiwfn_exe):
            QMessageBox.critical(self, "错误", f"Multiwfn 不存在:\n{multiwfn_exe}")
            return
        if not _HAS_MPL:
            QMessageBox.critical(
                self, "缺少依赖",
                "需要 matplotlib 和 numpy 来绘制柱形图。\n请运行: pip install matplotlib numpy"
            )
            return

        # Area distribution parameter dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("ESP 分区设置")
        dlg.setMinimumWidth(320)
        dlg.setStyleSheet("QDialog { background: #F8FAFC; }")
        layout = QVBoxLayout()
        form = QGridLayout()

        form.addWidget(QLabel("ESP 范围下限:"), 0, 0)
        rlo_edit = QLineEdit("-25")
        form.addWidget(rlo_edit, 0, 1)
        form.addWidget(QLabel("ESP 范围上限:"), 1, 0)
        rhi_edit = QLineEdit("22")
        form.addWidget(rhi_edit, 1, 1)
        form.addWidget(QLabel("分区数量:"), 2, 0)
        bins_edit = QLineEdit("15")
        form.addWidget(bins_edit, 2, 1)
        form.addWidget(QLabel("(默认 kcal/mol 单位)"), 3, 1)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.setLayout(layout)

        if not dlg.exec_():
            return

        try:
            range_low = float(rlo_edit.text())
            range_high = float(rhi_edit.text())
            n_bins = int(bins_edit.text())
        except ValueError:
            QMessageBox.critical(self, "错误", "请输入有效的数值")
            return
        if n_bins < 2 or n_bins > 50:
            QMessageBox.critical(self, "错误", "分区数量应在 2~50 之间")
            return

        self._log("")
        self._log("━━━ ESP 分区面积分布分析 ━━━")
        self._set_busy(True)
        self._set_status("正在计算 ESP 分面面积分布...")

        params = {
            'input_files': list(self.input_files),
            'multiwfn_exe': multiwfn_exe,
            'work_dir': self.work_dir,
            'range_low': range_low,
            'range_high': range_high,
            'n_bins': n_bins,
        }
        self._worker = AreaAnalysisWorker(params)
        self._worker.log_signal.connect(self._log)
        self._worker.progress_signal.connect(self._set_progress)
        self._worker.status_signal.connect(self._set_status)
        self._worker.busy_signal.connect(self._set_busy)
        self._worker.data_signal.connect(self._on_area_data_ready)
        self._worker.start()

    def _on_area_data_ready(self, all_data):
        """Show interactive matplotlib chart dialog after area computation."""
        self._log("")
        self._log("正在打开交互式图表窗口...")
        dlg = AreaChartDialog(all_data, self)
        dlg.exec_()

    def _run_job(self, action, output_path=None):
        params = self._get_params(action, output_path=output_path)
        self._worker = MultiwfnWorker(params)
        self._worker.log_signal.connect(self._log)
        self._worker.progress_signal.connect(self._set_progress)
        self._worker.status_signal.connect(self._set_status)
        self._worker.busy_signal.connect(self._set_busy)
        self._worker.finished_signal.connect(self._on_job_finished)
        self._worker.start()

    def _on_job_finished(self, result):
        self._set_busy(False)
        if result['ok']:
            if result['action'] == 'preview':
                self.vmd_port = result.get('vmd_port')
                self.vmd_process = result.get('vmd_proc')
                self.vmd_render_dir = result.get('vmd_dir')
                self.btn_render_view.setEnabled(True)
                if result.get('mode') in ('iso', 'all'):
                    self.opacity_slider.setEnabled(True)
                    self._log("ISO/ALL 模式：可拖动 [透明度] 滑块调整等值面透明度")
                if result.get('mode') in ('ext', 'all'):
                    self.btn_pick.setEnabled(True)
                    self._show_labels_cb.setEnabled(True)
                    self._log("EXT/ALL 模式：勾选 [显示极值点] 一键显示极值点及 ESP 数值")
                    self._log("EXT/ALL 模式：点击 [🔍 查询极值点] 可查看极值点 ESP 数值")
                    # Auto-show labels if checkbox was pre-checked
                    if self._show_labels_cb.isChecked():
                        size = self._label_size_spin.value()
                        offset = self._label_offset_spin.value()
                        QTimer.singleShot(4000, lambda s=size, o=offset: (
                            self._send_vmd_cmd_fast("show_extrema_points"),
                            self._send_vmd_cmd_fast(f"show_extrema_labels {s} {o}")
                        ))
            elif result['action'] == 'render':
                output_path = result.get('output_path', '')
                QMessageBox.information(self, "完成", f"渲染完成:\n{output_path}")
                self.btn_render_view.setEnabled(False)
                self.btn_pick.setEnabled(False)
                self._show_labels_cb.setEnabled(False)
                self.opacity_slider.setEnabled(False)

    # ══ VMD Pick Callback Server ══

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
                            self.pick_signal.emit(msg)
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
                x, y, z = float(xyz_parts[0]), float(xyz_parts[1]), float(xyz_parts[2])
            else:
                x = y = z = None

            if atom_name == "C":
                ext_type = "极大"
            elif atom_name == "O":
                ext_type = "极小"
            else:
                ext_type = f"原子({atom_name})"

            pdb_line = int(atom_idx) + 1
            unit = self.esp_unit

            if beta_val is not None:
                esp_str = f"{beta_val:+.2f} {unit}"
                self._log("-" * 50)
                self._log(f"  #{pdb_line:<4}  {ext_type:>6}值点   ESP = {esp_str}")
                if x is not None:
                    self._log(f"        坐标: ({x:8.3f}, {y:8.3f}, {z:8.3f})")
                self._log("-" * 50)
                self._set_status(f"ESP = {esp_str} ({ext_type}值点, index {atom_idx})")
            else:
                self._log(f"  📌 极值点 #{pdb_line} (index {atom_idx}): {ext_type}值点")
                if x is not None:
                    self._log(f"     坐标: ({x:.3f}, {y:.3f}, {z:.3f})")

        except (IndexError, ValueError) as e:
            self._log(f"  ⚠ 解析 VMD pick 数据失败: {msg} ({e})")


# ── QSS 样式 ────────────────────────────────────────────

LIGHT_QSS = """
/* ── Global ── */
QMainWindow {
    background-color: #E4EAF2;
}

QWidget {
    font-family: "Segoe UI", "Microsoft YaHei", "Consolas", sans-serif;
    font-size: 9pt;
    color: #2C3E50;
}

/* ── Group Box ── */
QGroupBox {
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    margin-top: 12px;
    padding: 14px 10px 10px 10px;
    background-color: #FFFFFF;
    font-weight: bold;
    font-size: 10pt;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 1px 10px 1px 10px;
    color: #FFFFFF;
    background-color: #1565C0;
    border-radius: 4px;
    font-size: 9pt;
}

/* ── Labels ── */
QLabel {
    color: #4A5568;
    padding: 1px 0px;
    margin: 0px;
}

QLabel#TitleLabel {
    color: #0D47A1;
    font-size: 16pt;
    font-weight: bold;
    padding: 6px 8px 2px 8px;
    qproperty-alignment: AlignCenter;
}

QLabel#SubTitleLabel {
    color: #5C6BC0;
    font-size: 8.5pt;
    padding: 0px 8px 8px 8px;
    qproperty-alignment: AlignCenter;
}

QLabel#ProgressLabel {
    color: #1565C0;
    font-size: 9pt;
    font-weight: bold;
    padding: 5px 12px;
    background-color: #EEF2FF;
    border: 1px solid #C5CAE9;
    border-radius: 4px;
}

QLabel#HintLabel {
    color: #7986CB;
    font-size: 8pt;
    padding: 1px 4px;
}

/* ── Line Edit ── */
QLineEdit {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 10px;
    color: #2C3E50;
    selection-background-color: #1E88E5;
    selection-color: #FFFFFF;
}

QLineEdit:focus {
    border: 1px solid #1E88E5;
    background-color: #F8FAFE;
}

QLineEdit:disabled {
    background-color: #F1F5F9;
    color: #94A3B8;
    border: 1px solid #E2E8F0;
}

/* ── Spin Box ── */
QSpinBox, QDoubleSpinBox {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 8px;
    color: #2C3E50;
    min-width: 60px;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #1E88E5;
    background-color: #F8FAFE;
}

QSpinBox:hover, QDoubleSpinBox:hover {
    border: 1px solid #5C6BC0;
}

QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #F1F5F9;
    color: #94A3B8;
    border: 1px solid #E2E8F0;
}

/* ── Combo Box ── */
QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    padding: 5px 10px;
    color: #2C3E50;
    min-width: 80px;
}

QComboBox:focus {
    border: 1px solid #1E88E5;
}

QComboBox:hover {
    border: 1px solid #5C6BC0;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #E2E8F0;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #F8FAFE;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    color: #2C3E50;
    selection-background-color: #E3F2FD;
    selection-color: #1565C0;
    outline: none;
}

QComboBox QAbstractItemView::item:hover {
    background-color: #E8EAF6;
    color: #1A237E;
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

QPushButton#PrimaryBtn {
    background-color: #1565C0;
    border: 1px solid #0D47A1;
    color: #FFFFFF;
    font-size: 10pt;
    padding: 8px 20px;
}

QPushButton#PrimaryBtn:hover {
    background-color: #1E88E5;
    border: 1px solid #1565C0;
    color: #FFFFFF;
}

QPushButton#PrimaryBtn:pressed {
    background-color: #0D47A1;
}

QPushButton#RenderBtn {
    background-color: #00897B;
    border: 1px solid #00695C;
    color: #FFFFFF;
    font-size: 10pt;
    padding: 8px 20px;
}

QPushButton#RenderBtn:hover {
    background-color: #26A69A;
    border: 1px solid #00897B;
    color: #FFFFFF;
}

QPushButton#RenderBtn:pressed {
    background-color: #00695C;
}

QPushButton#StopBtn {
    background-color: #FFFFFF;
    border: 1px solid #E53935;
    color: #E53935;
    font-size: 10pt;
    padding: 8px 20px;
}

QPushButton#StopBtn:hover {
    background-color: #FFEBEE;
    border: 1px solid #EF5350;
    color: #D32F2F;
}

QPushButton#StopBtn:pressed {
    background-color: #FFCDD2;
}

QPushButton#SmallBtn {
    padding: 3px 10px;
    font-size: 8pt;
    min-width: 34px;
}

QPushButton#SmallBtn:hover {
    background-color: #E3F2FD;
    border: 1px solid #1E88E5;
    color: #1565C0;
}

QPushButton#GhostBtn {
    background-color: transparent;
    border: none;
    color: #1565C0;
    font-weight: bold;
    padding: 4px 12px;
}

QPushButton#GhostBtn:hover {
    background-color: #E3F2FD;
}

/* ── Text Edit (Log) ── */
QTextEdit {
    background-color: #F5F6FA;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 8px 10px;
    color: #1E293B;
    font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
    font-size: 8.5pt;
    selection-background-color: #BBDEFB;
    selection-color: #0D47A1;
}

QTextEdit:focus {
    border: 1px solid #1E88E5;
}

QScrollBar:vertical {
    background-color: #F1F5F9;
    width: 10px;
    margin: 0;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #CBD5E1;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #1E88E5;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollBar:horizontal {
    background-color: #F1F5F9;
    height: 10px;
    margin: 0;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #CBD5E1;
    border-radius: 5px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #1E88E5;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: none;
}

/* ── Frame separator ── */
QFrame#Separator {
    background-color: #CBD5E1;
    max-height: 1px;
}

/* ── Scroll Area ── */
QScrollArea {
    border: none;
    background-color: transparent;
}

QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ── Viewer Frame ── */
QFrame#ViewerFrame {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
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

/* ── Tooltip ── */
QToolTip {
    background-color: #FFFFFF;
    border: 1px solid #1E88E5;
    border-radius: 4px;
    padding: 5px 10px;
    color: #2C3E50;
    font-size: 8.5pt;
}

/* ── Tab Widget ── */
QTabWidget::pane {
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    background-color: #FFFFFF;
    padding: 8px;
}

QTabBar::tab {
    background-color: #F1F5F9;
    border: 1px solid #CBD5E1;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 28px;
    margin-right: 3px;
    min-width: 80px;
    color: #4A5568;
    font-weight: bold;
    font-size: 9pt;
}

QTabBar::tab:selected {
    background-color: #FFFFFF;
    color: #1565C0;
    border-bottom: 2px solid #1E88E5;
}

QTabBar::tab:hover:!selected {
    background-color: #E3F2FD;
    color: #1565C0;
}

QTabBar::tab:disabled {
    color: #94A3B8;
    background-color: #F1F5F9;
}
"""


# ── Main ────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_QSS)
    window = ESPSurfaceGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()