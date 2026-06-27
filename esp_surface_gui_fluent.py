#!/usr/bin/env python3
"""
ESP Surface Visualization GUI v3.0 — Fluent Design Edition
PyQt-Fluent-Widgets + VMD + Multiwfn

Dependencies:
  pip install PyQt-Fluent-Widgets PyQt5
"""

import os, sys, re, glob, subprocess, threading, shutil, time, tempfile, socket, configparser, math

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon, QKeySequence, QColor
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QFileDialog, QDialog, QMessageBox, QFrame, QProgressBar,
    QTextEdit, QListWidget, QAbstractItemView, QStackedWidget,
)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon,
    PushButton, PrimaryPushButton, TransparentPushButton,
    ComboBox, SpinBox, DoubleSpinBox, Slider, ToggleButton,
    LineEdit, TextEdit, PlainTextEdit,
    RadioButton, CheckBox, SwitchButton,
    CardWidget, GroupHeaderCardWidget,
    InfoBar, InfoBarPosition, ProgressBar, MessageBox, MessageBoxBase,
    TeachingTip, TeachingTipTailPosition, Flyout, FlyoutAnimationType,
    BodyLabel, CaptionLabel, StrongBodyLabel, TitleLabel, SubtitleLabel,
    setTheme, Theme, setThemeColor, isDarkTheme, qconfig,
    SmoothScrollArea, ScrollArea, SingleDirectionScrollArea,
    PixmapLabel, TransparentToolButton, PrimaryToolButton,
    FluentStyleSheet, StateToolTip,
)

# ── Optional dependencies ──
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import numpy as np
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

AU = 627.509

# ── Default Paths ──
DEFAULT_MULTIWFN = r"E:\Multiwfn_2026.4.10_bin_Win64\Multiwfn.exe"
DEFAULT_VMD = r"C:\Program Files (x86)\University of Illinois\VMD\vmd.exe"
DEFAULT_TACHYON = r"C:\Program Files (x86)\University of Illinois\VMD\tachyon_WIN32.exe"

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__)),
    "esp_viewer_config.ini"
)

# ── i18n ──
TR = {
    "win_title": {"zh": "ESPViewer — 分子表面静电势可视化", "en": "ESPViewer — Molecular Surface ESP Visualization"},
    "tab_analysis": {"zh": "分析与绘图", "en": "Analysis & Plot"},
    "tab_path": {"zh": "路径设置", "en": "Paths"},
    "input_files": {"zh": "输入文件", "en": "Input Files"},
    "path_settings": {"zh": "路径设置", "en": "Path Settings"},
    "mode_settings": {"zh": "分析模式与设置", "en": "Mode & Settings"},
    "run_log": {"zh": "运行日志", "en": "Run Log"},
    "add_file": {"zh": "添加文件", "en": "Add File(s)"},
    "add_dir": {"zh": "添加目录", "en": "Add Directory"},
    "clear_list": {"zh": "清空列表", "en": "Clear List"},
    "save_cfg": {"zh": "保存路径配置", "en": "Save Config"},
    "detect_range": {"zh": "检测 ESP 范围", "en": "Detect ESP Range"},
    "mode": {"zh": "模式:", "en": "Mode:"},
    "color_low": {"zh": "色彩下限:", "en": "Low:"},
    "color_high": {"zh": "色彩上限:", "en": "High:"},
    "hint_unit": {"zh": "(单位: kcal/mol)", "en": "(unit: kcal/mol)"},
    "pt_size": {"zh": "点大小(PT):", "en": "Point Size:"},
    "resolution": {"zh": "渲染分辨率:", "en": "Resolution:"},
    "nthreads": {"zh": "并行线程:", "en": "Threads:"},
    "nthreads_hint": {"zh": "按自己电脑情况设置", "en": "Set per your CPU"},
    "opacity": {"zh": "透明度:", "en": "Opacity:"},
    "colorbar": {"zh": "显示色彩刻度轴", "en": "Show Color Scale Bar"},
    "show_labels": {"zh": "显示极值数值", "en": "Show ESP Values"},
    "label_size": {"zh": "字号:", "en": "Size:"},
    "label_offset": {"zh": "偏移:", "en": "Offset:"},
    "pt_mode": {"zh": "PT 顶点着色", "en": "PT (Vertex)"},
    "iso_mode": {"zh": "ISO 等值面着色", "en": "ISO (Isosurface)"},
    "ext_mode": {"zh": "EXT 极值点", "en": "EXT (Extrema)"},
    "all_mode": {"zh": "ALL 全部叠加", "en": "ALL (Overlay)"},
    "btn_preview": {"zh": "▶ VMD 预览", "en": "▶ Preview"},
    "btn_render_view": {"zh": "📷 渲染当前视角", "en": "📷 Render View"},
    "btn_pick": {"zh": "🔍 查询极值点", "en": "🔍 Query Extrema"},
    "btn_render": {"zh": "▼ 渲染输出", "en": "▼ Batch Render"},
    "btn_area": {"zh": "📊 ESP分区面积图", "en": "📊 Area Chart"},
    "btn_stop": {"zh": "■ 停止", "en": "■ Stop"},
    "lang_btn": {"zh": "EN", "en": "中文"},
}


# ═══════════════════════════════════════════════════════════════
# Workers (QThread-based) — same logic as v2.0
# ═══════════════════════════════════════════════════════════════

class MultiwfnWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, fchk_path, task_code, mwfn_exe, params=None):
        super().__init__()
        self.fchk_path = fchk_path
        self.task_code = task_code
        self.mwfn_exe = mwfn_exe
        self.params = params or {}
        self.nthreads = self.params.get('nthreads', 14)

    @staticmethod
    def _apply_multiwfn_nthreads(mwfn_base, nthreads):
        settings_path = os.path.join(os.path.dirname(mwfn_base), "settings.ini")
        if not os.path.exists(settings_path):
            return
        try:
            content = open(settings_path, 'r').read()
            content = re.sub(r'^nthreads\s*=\s*\d+', f'nthreads=  {nthreads}', content, flags=re.MULTILINE)
            if 'nthreads=' not in content:
                content = content.rstrip() + f'\nnthreads=  {nthreads}\n'
            open(settings_path, 'w').write(content)
        except Exception:
            pass

    def run(self):
        try:
            mwfn_base = self.mwfn_exe
            self._apply_multiwfn_nthreads(os.path.dirname(mwfn_base), self.nthreads)
            mwfn_dir = os.path.dirname(mwfn_base)
            fchk_base = os.path.basename(self.fchk_path)

            cmd_lines = self.task_code.strip().split('\n')
            full_input = '\n'.join(cmd_lines) + '\n'

            proc = subprocess.run(
                [mwfn_base, fchk_base],
                input=full_input, text=True, capture_output=True, cwd=mwfn_dir,
                timeout=600, shell=False,
            )

            stdout = proc.stdout
            stderr = proc.stderr

            for line in stdout.split('\n'):
                if line.strip():
                    self.log_signal.emit(line.strip())
                if '%' in line and 'finished' in line.lower():
                    pct_match = re.search(r'(\d+)%', line)
                    if pct_match:
                        self.progress_signal.emit(int(pct_match.group(1)))

            if proc.returncode != 0 and stderr.strip():
                self.error_signal.emit(stderr.strip()[:500])
            elif 'error' in stdout.lower()[-200:].lower():
                self.error_signal.emit(stdout[-500:])
            else:
                self.finished_signal.emit(stdout)
        except subprocess.TimeoutExpired:
            self.error_signal.emit("Multiwfn timed out (>600s)")
        except Exception as e:
            self.error_signal.emit(str(e))


class VMDController:
    """Controls VMD via Tcl socket."""

    def __init__(self, host='localhost', port=7777):
        self.host = host
        self.port = port
        self._sock = None

    def connect(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect((self.host, self.port))
            return True
        except Exception:
            self._sock = None
            return False

    def send(self, cmd):
        if not self._sock:
            return False
        try:
            self._sock.sendall((cmd + '\n').encode())
            return True
        except Exception:
            return False

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class AreaAnalysisWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    data_signal = pyqtSignal(list)

    def __init__(self, fchk_paths, mwfn_exe, clow, chigh, n_bins=30):
        super().__init__()
        self.fchk_paths = fchk_paths
        self.mwfn_exe = mwfn_exe
        self.clow = clow
        self.chigh = chigh
        self.n_bins = n_bins

    def run(self):
        all_data = []
        total = len(self.fchk_paths)
        for idx, fchk in enumerate(self.fchk_paths):
            self.progress_signal.emit(int((idx / total) * 80))
            self.log_signal.emit(f"Analyzing: {os.path.basename(fchk)}")
            data = self._analyze_one(fchk)
            if data:
                all_data.append((os.path.basename(fchk), data))
            self.progress_signal.emit(int(((idx + 1) / total) * 100))
        self.data_signal.emit(all_data)

    def _analyze_one(self, fchk_path):
        mwfn_dir = os.path.dirname(self.mwfn_exe)
        fchk_base = os.path.basename(fchk_path)
        task = f"12\n7\n0\n{self.clow}\n{self.chigh}\n{self.n_bins}\n0\nq\n"
        try:
            proc = subprocess.run(
                [self.mwfn_exe, fchk_base],
                input=task, text=True, capture_output=True, cwd=mwfn_dir,
                timeout=600,
            )
            return self._parse_area_output(proc.stdout)
        except Exception:
            return None

    def _parse_area_output(self, stdout):
        """Parse Multiwfn output for bin center and area."""
        import re
        results = []
        pattern = re.compile(
            r'^\s*([-\d.]+)\s+to\s+([-\d.]+)\s+([\d.]+)\s+(\d+\.\d+)\s*$'
        )
        for line in stdout.split('\n'):
            m = pattern.match(line)
            if m:
                lo, hi = float(m.group(1)), float(m.group(2))
                area = float(m.group(3))
                center = (lo + hi) / 2.0
                results.append((center, area))
        return results


# ═══════════════════════════════════════════════════════════════
#  Area Chart Dialog (matplotlib)
# ═══════════════════════════════════════════════════════════════

if _HAS_MPL:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
    import matplotlib as mpl
    mpl.rcParams['font.family'] = 'Arial'

    class AreaChartDialog(QDialog):
        def __init__(self, all_data, parent=None):
            super().__init__(parent)
            self.setWindowTitle("ESP Area Distribution Chart")
            self.setMinimumSize(750, 780)
            self.resize(850, 850)
            self.all_data = all_data
            self._overlay_mode = len(all_data) > 1

            layout = QVBoxLayout(self)

            # ── Controls ──
            top = QHBoxLayout()
            top.addWidget(QLabel("标题:"))
            self.title_edit = QLineEdit("ESP Area Distribution")
            self.title_edit.textChanged.connect(self._refresh_chart)
            top.addWidget(self.title_edit, stretch=2)

            top.addWidget(QLabel("X轴:"))
            self.xlabel_edit = QLineEdit("Electrostatic potential (kcal/mol)")
            self.xlabel_edit.textChanged.connect(self._refresh_chart)
            top.addWidget(self.xlabel_edit, stretch=1)

            top.addWidget(QLabel("Y轴:"))
            self.ylabel_edit = QLineEdit("Surface Area (Å²)")
            self.ylabel_edit.textChanged.connect(self._refresh_chart)
            top.addWidget(self.ylabel_edit, stretch=1)

            if len(all_data) > 1:
                top.addWidget(QLabel("文件:"))
                self.file_combo = QComboBox()
                self.file_combo.addItems([d[0] for d in all_data])
                self.file_combo.currentIndexChanged.connect(self._on_file_changed)
                top.addWidget(self.file_combo)

            layout.addLayout(top)

            # ── Font & display controls ──
            ctrl_row = QHBoxLayout()
            ctrl_row.addWidget(QLabel("坐标字号:"))
            self.tick_fs_spin = SpinBox(self)
            self.tick_fs_spin.setRange(6, 20)
            self.tick_fs_spin.setValue(9)
            self.tick_fs_spin.setMaximumWidth(60)
            self.tick_fs_spin.valueChanged.connect(self._refresh_chart)
            ctrl_row.addWidget(self.tick_fs_spin)

            ctrl_row.addWidget(QLabel("数值字号:"))
            self.bar_fs_spin = SpinBox(self)
            self.bar_fs_spin.setRange(5, 18)
            self.bar_fs_spin.setValue(7)
            self.bar_fs_spin.setMaximumWidth(60)
            self.bar_fs_spin.valueChanged.connect(self._refresh_chart)
            ctrl_row.addWidget(self.bar_fs_spin)

            self._show_bar_val_cb = CheckBox("显示数值")
            self._show_bar_val_cb.setChecked(True)
            self._show_bar_val_cb.stateChanged.connect(self._refresh_chart)
            ctrl_row.addWidget(self._show_bar_val_cb)

            if len(all_data) > 1:
                self._overlay_cb = CheckBox("叠加对比")
                self._overlay_cb.setChecked(False)
                self._overlay_cb.stateChanged.connect(self._on_overlay_toggle)
                ctrl_row.addWidget(self._overlay_cb)

            ctrl_row.addStretch()
            layout.addLayout(ctrl_row)

            # ── Matplotlib canvas ──
            self.fig, self._ax = plt.subplots(figsize=(7, 7))
            self._ax.spines['top'].set_visible(True)
            self._ax.spines['right'].set_visible(True)
            for spine in self._ax.spines.values():
                spine.set_color('black')
                spine.set_linewidth(0.8)

            self.canvas = FigureCanvasQTAgg(self.fig)
            self.toolbar = NavigationToolbar2QT(self.canvas, self)
            layout.addWidget(self.toolbar)
            layout.addWidget(self.canvas)

            self._refresh_chart()

        def _on_file_changed(self, idx):
            if not self._overlay_cb.isChecked():
                self._refresh_chart()

        def _on_overlay_toggle(self):
            self.file_combo.setEnabled(not self._overlay_cb.isChecked())
            self._refresh_chart()

        def _refresh_chart(self):
            self._ax.clear()
            self._ax.spines['top'].set_visible(True)
            self._ax.spines['right'].set_visible(True)
            for spine in self._ax.spines.values():
                spine.set_color('black')
                spine.set_linewidth(0.8)

            if self._overlay_cb.isChecked() if hasattr(self, '_overlay_cb') else False:
                self._draw_overlay()
            else:
                idx = self.file_combo.currentIndex() if hasattr(self, 'file_combo') else 0
                _, data = self.all_data[idx]
                self._draw_single(data)
            self.canvas.draw()

        def _draw_single(self, data):
            centers = [d[0] for d in data]
            areas = [d[1] for d in data]
            n = len(areas)
            if n == 0:
                return
            if n > 1:
                bin_width = centers[1] - centers[0]
            else:
                bin_width = 1.0

            colors = plt.cm.RdBu_r(np.linspace(0.15, 0.85, n))

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

            self._ax.set_xlim(centers[0] - bin_width, centers[-1] + bin_width)
            self._ax.set_ylim(0, max_area * 1.18)

        def _draw_overlay(self):
            mol_colors = ['#E64A19', '#1976D2', '#388E3C', '#7B1FA2',
                          '#F9A825', '#00838F', '#D81B60', '#5D4037']
            all_bars = []
            all_centers = None
            bin_width = None
            max_val = 0
            labels = []

            for i, (name, data) in enumerate(self.all_data):
                centers = [d[0] for d in data]
                areas = [d[1] for d in data]
                if all_centers is None:
                    all_centers = centers
                    if len(centers) > 1:
                        bin_width = centers[1] - centers[0]
                    else:
                        bin_width = 1.0

                n = len(centers)
                w = bin_width * 0.85 / len(self.all_data)
                offset = (i - (len(self.all_data) - 1) / 2) * w

                bars = self._ax.bar(
                    [c + offset for c in centers], areas, width=w * 0.92,
                    align='center', color=mol_colors[i % len(mol_colors)],
                    edgecolor='#222222', linewidth=0.5, label=name,
                )
                all_bars.append(bars)
                labels.append(name)
                if areas:
                    max_val = max(max_val, max(areas))

            self._ax.set_xlabel(self.xlabel_edit.text() or "ESP", fontsize=12)
            self._ax.set_ylabel(self.ylabel_edit.text() or "Area", fontsize=12)
            self._ax.set_title(self.title_edit.text() or "Area Distribution",
                               fontsize=14, fontweight='bold', pad=12)
            self._ax.set_xticks(all_centers)
            tick_fs = self.tick_fs_spin.value()
            self._ax.set_xticklabels([f'{c:.1f}' for c in all_centers], rotation=45, ha='right', fontsize=tick_fs)
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

            if all_centers and bin_width:
                self._ax.set_xlim(all_centers[0] - bin_width, all_centers[-1] + bin_width)
            self._ax.set_ylim(0, max_val * 1.18)
            self._ax.legend(loc='upper right', fontsize=9, frameon=True)


# ═══════════════════════════════════════════════════════════════
#  Analysis Page
# ═══════════════════════════════════════════════════════════════

class AnalysisPage(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._lang = "zh"
        self._files = []
        self._file_names = []
        self._vmd = VMDController()
        self._mwfn_worker = None
        self._area_worker = None
        self._vmd_lock = threading.Lock()
        self._iso_in_use = False

        self._mode_map = {"pt": 0, "iso": 1, "ext": 2, "all": 3}
        self._current_mode = "pt"

        self._setup_ui()

    def _tr(self, key, **fmt):
        s = TR.get(key, {}).get(self._lang, key)
        return s.format(**fmt) if fmt else s

    # ── UI Setup ──
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ── Row 1: File input ──
        file_card = CardWidget(self)
        file_layout = QVBoxLayout(file_card)
        file_layout.setContentsMargins(16, 12, 16, 12)

        file_title_row = QHBoxLayout()
        file_title_row.addWidget(SubtitleLabel(self._tr("input_files")))
        self._lang_btn = PrimaryPushButton(self._tr("lang_btn"))
        self._lang_btn.clicked.connect(self._switch_lang)
        file_title_row.addWidget(self._lang_btn)
        file_title_row.addStretch()
        file_layout.addLayout(file_title_row)

        btn_row = QHBoxLayout()
        self._btn_add = PushButton(FluentIcon.ADD, self._tr("add_file"))
        self._btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(self._btn_add)

        self._btn_adddir = PushButton(FluentIcon.FOLDER, self._tr("add_dir"))
        self._btn_adddir.clicked.connect(self._add_dir)
        btn_row.addWidget(self._btn_adddir)

        self._btn_clear = PushButton(FluentIcon.DELETE, self._tr("clear_list"))
        self._btn_clear.clicked.connect(self._clear_files)
        btn_row.addWidget(self._btn_clear)

        self.lbl_filecount = BodyLabel("(0 files)")
        btn_row.addWidget(self.lbl_filecount)
        btn_row.addStretch()
        file_layout.addLayout(btn_row)

        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(80)
        self.file_list.setStyleSheet(
            "QListWidget { border: 1px solid rgba(128,128,128,80); border-radius: 4px; }"
        )
        file_layout.addWidget(self.file_list)
        main_layout.addWidget(file_card)

        # ── Row 2: Mode settings ──
        mode_card = CardWidget(self)
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(16, 12, 16, 12)
        mode_layout.addWidget(SubtitleLabel(self._tr("mode_settings")))

        # Mode radio buttons + colorbar
        mode_row = QHBoxLayout()
        self._lbl_mode = BodyLabel(self._tr("mode"))
        mode_row.addWidget(self._lbl_mode)

        self.mode_group = None
        self._rb_pt = RadioButton(self._tr("pt_mode"))
        self._rb_iso = RadioButton(self._tr("iso_mode"))
        self._rb_ext = RadioButton(self._tr("ext_mode"))
        self._rb_all = RadioButton(self._tr("all_mode"))
        self._rb_pt.setChecked(True)

        for rb in (self._rb_pt, self._rb_iso, self._rb_ext, self._rb_all):
            rb.toggled.connect(self._on_mode_change)
            mode_row.addWidget(rb)

        self.colorbar_cb = CheckBox(self._tr("colorbar"))
        self.colorbar_cb.setChecked(False)
        self.colorbar_cb.stateChanged.connect(self._on_colorbar_toggle)
        mode_row.addWidget(self.colorbar_cb)
        mode_row.addStretch()
        mode_layout.addLayout(mode_row)

        # Color range row
        range_row = QHBoxLayout()
        range_row.addWidget(BodyLabel(self._tr("color_low")))
        self.clow_edit = LineEdit()
        self.clow_edit.setText("-50")
        self.clow_edit.setMaximumWidth(80)
        range_row.addWidget(self.clow_edit)

        range_row.addWidget(BodyLabel(self._tr("color_high")))
        self.chigh_edit = LineEdit()
        self.chigh_edit.setText("50")
        self.chigh_edit.setMaximumWidth(80)
        range_row.addWidget(self.chigh_edit)

        self._hint_lbl = CaptionLabel(self._tr("hint_unit"))
        range_row.addWidget(self._hint_lbl)

        self._btn_detect = PushButton(self._tr("detect_range"))
        self._btn_detect.clicked.connect(self._action_detect_range)
        range_row.addWidget(self._btn_detect)
        range_row.addStretch()
        mode_layout.addLayout(range_row)

        # Point size + Resolution + Threads
        param_row = QHBoxLayout()
        param_row.addWidget(BodyLabel(self._tr("pt_size")))
        self.ptsize_edit = LineEdit()
        self.ptsize_edit.setText("2.0")
        self.ptsize_edit.setMaximumWidth(60)
        param_row.addWidget(self.ptsize_edit)

        param_row.addWidget(BodyLabel(self._tr("resolution")))
        self.res_combo = ComboBox()
        self.res_combo.addItems(["2000x1500", "1200x900", "3000x2250", "4000x3000"])
        self.res_combo.setCurrentText("2000x1500")
        self.res_combo.setMaximumWidth(130)
        param_row.addWidget(self.res_combo)

        param_row.addWidget(BodyLabel(self._tr("nthreads")))
        self.nthreads_spin = SpinBox(self)
        self.nthreads_spin.setRange(1, 64)
        self.nthreads_spin.setValue(14)
        self.nthreads_spin.setMaximumWidth(70)
        param_row.addWidget(self.nthreads_spin)

        nthreads_hint = CaptionLabel(self._tr("nthreads_hint"))
        param_row.addWidget(nthreads_hint)
        param_row.addStretch()
        mode_layout.addLayout(param_row)

        # Colorbar + extremum label controls
        ext_row = QHBoxLayout()
        self._show_labels_cb = CheckBox(self._tr("show_labels"))
        self._show_labels_cb.setChecked(False)
        self._show_labels_cb.stateChanged.connect(self._on_show_labels_toggle)
        ext_row.addWidget(self._show_labels_cb)

        ext_row.addWidget(BodyLabel(self._tr("label_size")))
        self._label_size_spin = DoubleSpinBox(self)
        self._label_size_spin.setRange(1.0, 5.0)
        self._label_size_spin.setSingleStep(0.1)
        self._label_size_spin.setValue(1.5)
        self._label_size_spin.setMaximumWidth(60)
        self._label_size_spin.valueChanged.connect(self._on_label_size_changed)
        ext_row.addWidget(self._label_size_spin)

        ext_row.addWidget(BodyLabel(self._tr("label_offset")))
        self._label_offset_spin = DoubleSpinBox(self)
        self._label_offset_spin.setRange(0.2, 3.0)
        self._label_offset_spin.setSingleStep(0.1)
        self._label_offset_spin.setValue(0.8)
        self._label_offset_spin.setMaximumWidth(60)
        self._label_offset_spin.valueChanged.connect(self._on_label_offset_changed)
        ext_row.addWidget(self._label_offset_spin)
        ext_row.addStretch()
        mode_layout.addLayout(ext_row)

        # Opacity slider
        op_row = QHBoxLayout()
        self._lbl_opacity = BodyLabel(self._tr("opacity"))
        op_row.addWidget(self._lbl_opacity)

        self.opacity_slider = Slider(Qt.Horizontal, self)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(70)
        self.opacity_slider.setEnabled(False)
        self.opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        op_row.addWidget(self.opacity_slider, stretch=1)

        self.opacity_value_label = BodyLabel("0.70")
        self.opacity_value_label.setMinimumWidth(40)
        op_row.addWidget(self.opacity_value_label)
        mode_layout.addLayout(op_row)

        main_layout.addWidget(mode_card)

        # ── Row 3: Action buttons ──
        btn_card = CardWidget(self)
        btn_layout = QHBoxLayout(btn_card)
        btn_layout.setContentsMargins(16, 12, 16, 12)

        self.btn_preview = PrimaryPushButton(FluentIcon.PLAY, self._tr("btn_preview"))
        self.btn_preview.clicked.connect(self._action_preview)
        btn_layout.addWidget(self.btn_preview)

        self.btn_render_view = PushButton(FluentIcon.CAMERA, self._tr("btn_render_view"))
        self.btn_render_view.setEnabled(False)
        self.btn_render_view.clicked.connect(self._action_render_view)
        btn_layout.addWidget(self.btn_render_view)

        self.btn_pick = PushButton(FluentIcon.SEARCH, self._tr("btn_pick"))
        self.btn_pick.setEnabled(False)
        self.btn_pick.clicked.connect(self._action_pick)
        btn_layout.addWidget(self.btn_pick)

        self.btn_render_out = PushButton(FluentIcon.SAVE, self._tr("btn_render"))
        self.btn_render_out.clicked.connect(self._action_render)
        btn_layout.addWidget(self.btn_render_out)

        self.btn_area = PushButton(FluentIcon.PIE_SINGLE, self._tr("btn_area"))
        self.btn_area.clicked.connect(self._action_area_chart)
        btn_layout.addWidget(self.btn_area)

        self.btn_stop = PushButton(FluentIcon.CANCEL, self._tr("btn_stop"))
        self.btn_stop.clicked.connect(self._action_stop)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addStretch()
        main_layout.addWidget(btn_card)

        # ── Progress ──
        prog_row = QHBoxLayout()
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setValue(0)
        prog_row.addWidget(self.progress_bar)
        self.lbl_status = CaptionLabel("Ready")
        prog_row.addWidget(self.lbl_status)
        main_layout.addLayout(prog_row)

        # ── Log ──
        log_card = CardWidget(self)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 8, 12, 8)
        log_layout.addWidget(SubtitleLabel(self._tr("run_log")))
        self.log_edit = PlainTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(500)
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_card, stretch=1)

    # ── UI Callbacks ──
    def _log(self, msg):
        self.log_edit.appendPlainText(msg)
        self.log_signal.emit(msg)

    def _set_progress(self, value):
        self.progress_bar.setValue(max(0, min(100, value)))

    def _set_status(self, msg):
        self.lbl_status.setText(msg)

    def _set_busy(self, busy=True):
        for btn in (self.btn_preview, self.btn_render_out, self.btn_area):
            btn.setEnabled(not busy)
        if busy:
            self.btn_render_view.setEnabled(False)
            self.btn_pick.setEnabled(False)
            self.opacity_slider.setEnabled(False)

    # ── i18n ──
    def _switch_lang(self):
        self._lang = "en" if self._lang == "zh" else "zh"
        self._apply_lang_ui()

    def _apply_lang_ui(self):
        self._lang_btn.setText(self._tr("lang_btn"))
        self._btn_add.setText(self._tr("add_file"))
        self._btn_adddir.setText(self._tr("add_dir"))
        self._btn_clear.setText(self._tr("clear_list"))
        self._lbl_mode.setText(self._tr("mode"))
        self._rb_pt.setText(self._tr("pt_mode"))
        self._rb_iso.setText(self._tr("iso_mode"))
        self._rb_ext.setText(self._tr("ext_mode"))
        self._rb_all.setText(self._tr("all_mode"))
        self.colorbar_cb.setText(self._tr("colorbar"))
        self._hint_lbl.setText(self._tr("hint_unit"))
        self._btn_detect.setText(self._tr("detect_range"))
        self._show_labels_cb.setText(self._tr("show_labels"))
        self._lbl_opacity.setText(self._tr("opacity"))
        self.btn_preview.setText(self._tr("btn_preview"))
        self.btn_render_view.setText(self._tr("btn_render_view"))
        self.btn_pick.setText(self._tr("btn_pick"))
        self.btn_render_out.setText(self._tr("btn_render"))
        self.btn_area.setText(self._tr("btn_area"))
        self.btn_stop.setText(self._tr("btn_stop"))

    def _browse_exe(self, edit_widget, name):
        p, _ = QFileDialog.getOpenFileName(self, f"Select {name}", "", "EXE (*.exe);;All (*)")
        if p:
            edit_widget.setText(p)

    # ── File management ──
    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select .fch/.fchk files", "",
            "Formatted Checkpoint (*.fch *.fchk);;All (*)"
        )
        for f in files:
            if f not in self._files:
                self._files.append(f)
                self._file_names.append(os.path.basename(f))
        self._refresh_file_list()

    def _add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select directory with .fch files")
        if d:
            for f in sorted(glob.glob(os.path.join(d, "*.fch")) + glob.glob(os.path.join(d, "*.fchk"))):
                if f not in self._files:
                    self._files.append(f)
                    self._file_names.append(os.path.basename(f))
        self._refresh_file_list()

    def _clear_files(self):
        self._files.clear()
        self._file_names.clear()
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.file_list.clear()
        self.file_list.addItems(self._file_names)
        self.lbl_filecount.setText(f"({len(self._files)} files)")

    # ── Mode ──
    def _on_mode_change(self):
        if self._rb_pt.isChecked():
            self._current_mode = "pt"
        elif self._rb_iso.isChecked():
            self._current_mode = "iso"
        elif self._rb_ext.isChecked():
            self._current_mode = "ext"
        elif self._rb_all.isChecked():
            self._current_mode = "all"

        iso_modes = self._current_mode in ("iso", "all")
        self.opacity_slider.setEnabled(iso_modes)

    def _on_colorbar_toggle(self):
        pass

    def _on_show_labels_toggle(self):
        if not self._show_labels_cb.isChecked():
            self._send_vmd_cmd('if {[info commands show_extrema_labels] ne ""} { show_extrema_labels 0.0 0.0 }')
            self._log("Extrema labels cleared")
        else:
            self._apply_label_settings()

    def _apply_label_settings(self):
        size = self._label_size_spin.value()
        offset = self._label_offset_spin.value()
        self._send_vmd_cmd(f"show_extrema_labels {size} {offset}")

    def _on_label_size_changed(self, val):
        if self._show_labels_cb.isChecked():
            self._apply_label_settings()

    def _on_label_offset_changed(self, val):
        if self._show_labels_cb.isChecked():
            self._apply_label_settings()

    def _on_opacity_slider_changed(self, val):
        opacity = val / 100.0
        self.opacity_value_label.setText(f"{opacity:.2f}")
        self._send_vmd_cmd(f"mol modstyle 0 0 CPK 0.3 0.2 0.5 0.5")  # simplified

    # ── VMD socket ──
    def _send_vmd_cmd(self, cmd):
        self._vmd.send(cmd)

    # ── ESP Range Detection ──
    def _action_detect_range(self):
        if not self._files:
            InfoBar.warning(
                title='No files', content='Please add .fch files first.',
                orient=Qt.Horizontal, isClosable=True, duration=3000,
                position=InfoBarPosition.TOP_RIGHT, parent=self
            )
            return
        mwfn_exe = self.config.get("multiwfn", DEFAULT_MULTIWFN)
        if not os.path.exists(mwfn_exe):
            self._log("Multiwfn not found!")
            return
        fchk = self._files[0]
        self._log(f"Detecting ESP range for: {os.path.basename(fchk)}")
        self._set_status("Detecting ESP range...")

        task = "12\n7\n0\n-100\n100\n10\n0\nq\n"
        self._mwfn_worker = MultiwfnWorker(fchk, task, mwfn_exe)
        self._mwfn_worker.log_signal.connect(self._log)
        self._mwfn_worker.finished_signal.connect(self._on_range_detected)
        self._mwfn_worker.error_signal.connect(lambda e: self._log(f"Error: {e}"))
        self._mwfn_worker.start()

    def _on_range_detected(self, stdout):
        clow, chigh = self._parse_esp_range(stdout)
        if clow is not None:
            self.clow_edit.setText(f"{clow:.1f}")
            self.chigh_edit.setText(f"{chigh:.1f}")
            self._log(f"Detected ESP range: {clow:.1f} ~ {chigh:.1f} kcal/mol")
        self._set_status("Ready")

    def _parse_esp_range(self, stdout):
        for line in stdout.split('\n'):
            m = re.search(r'Minimum\s*:\s*([\d.-]+)\s*.*Maximum\s*:\s*([\d.-]+)', line)
            if m:
                vmin, vmax = float(m.group(1)), float(m.group(2))
                # Check if in a.u. (small values)
                if abs(vmin) < 5 and abs(vmax) < 5:
                    return vmin * AU, vmax * AU
                return vmin, vmax
            m = re.search(r'from\s*([\d.-]+)\s*to\s*([\d.-]+)\s*kcal/mol', line)
            if m:
                return float(m.group(1)), float(m.group(2))
        return None

    # ── Preview ──
    def _action_preview(self):
        if not self._files:
            InfoBar.warning(
                title='No files', content='Please add .fch files first.',
                orient=Qt.Horizontal, isClosable=True, duration=3000,
                position=InfoBarPosition.TOP_RIGHT, parent=self
            )
            return
        mwfn_dir = os.path.dirname(self.config.get("multiwfn", DEFAULT_MULTIWFN))
        fchk = self._files[0]
        self._log(f"Starting VMD preview: {os.path.basename(fchk)}")
        self._set_busy(True)
        self._set_status("Generating ESP surface...")

        task = self._build_multiwfn_task()
        self._mwfn_worker = MultiwfnWorker(
            fchk, task, self.config.get("multiwfn", DEFAULT_MULTIWFN),
            params={'nthreads': self.nthreads_spin.value()}
        )
        self._mwfn_worker.log_signal.connect(self._log)
        self._mwfn_worker.finished_signal.connect(self._on_preview_done)
        self._mwfn_worker.error_signal.connect(lambda e: self._log(f"Error: {e}"))
        self._mwfn_worker.start()

    def _build_multiwfn_task(self):
        """Build Multiwfn input for the current mode."""
        mode = self._current_mode
        clow = float(self.clow_edit.text() or "-50")
        chigh = float(self.chigh_edit.text() or "50")
        ptsize = float(self.ptsize_edit.text() or "2.0")

        # Convert kcal/mol to a.u. for Multiwfn
        clow_au = clow / AU
        chigh_au = chigh / AU

        if mode == "pt":
            return f"12\n7\n1\n{clow_au:.8f}\n{chigh_au:.8f}\n{ptsize:.1f}\n0\nq\n"
        elif mode == "iso":
            self._iso_in_use = True
            return f"12\n7\n2\n{clow_au:.8f}\n{chigh_au:.8f}\n{ptsize:.1f}\n0\nq\n"
        elif mode == "ext":
            return f"12\n7\n3\n{clow_au:.8f}\n{chigh_au:.8f}\n{ptsize:.1f}\n0\nq\n"
        else:  # all
            return f"12\n7\n4\n{clow_au:.8f}\n{chigh_au:.8f}\n{ptsize:.1f}\n0\nq\n"

    def _on_preview_done(self, stdout):
        self._log("Multiwfn done. Launching VMD...")
        vmd_exe = self.config.get("vmd", DEFAULT_VMD)
        vmd_dir = self.config.get("vmd_dir", os.path.dirname(self.config.get("multiwfn", DEFAULT_MULTIWFN)))

        if not os.path.exists(vmd_exe):
            self._log(f"VMD not found at: {vmd_exe}")
            self._set_busy(False)
            return

        # Copy surface files to VMD dir and launch VMD
        mwfn_dir = os.path.dirname(self.config.get("multiwfn", DEFAULT_MULTIWFN))
        try:
            for fn in os.listdir(mwfn_dir):
                if fn.endswith('.pdb') or fn.endswith('.cub') or fn.endswith('.tga'):
                    src = os.path.join(mwfn_dir, fn)
                    dst = os.path.join(vmd_dir, fn)
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
        except Exception as e:
            self._log(f"Copy warning: {e}")

        # Launch VMD in background
        threading.Thread(target=self._launch_vmd, daemon=True).start()
        self._set_status("VMD launched")
        self._set_busy(False)
        self.btn_render_view.setEnabled(True)
        self.btn_pick.setEnabled(True)

    def _launch_vmd(self):
        vmd_exe = self.config.get("vmd", DEFAULT_VMD)
        vmd_dir = self.config.get("vmd_dir", os.path.dirname(self.config.get("multiwfn", DEFAULT_MULTIWFN)))

        try:
            subprocess.Popen([vmd_exe, "-e", "surfanalysis.vmd"], cwd=vmd_dir,
                             shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            if self._vmd.connect():
                self._log("VMD socket connected (port 7777)")
                if self._current_mode in ("iso", "all"):
                    self._send_vmd_cmd("mol scaleminmax 0 0 [expr {1.0/627.509*" +
                                       self.clow_edit.text() + "}] [expr {1.0/627.509*" +
                                       self.chigh_edit.text() + "}]")
                if self.colorbar_cb.isChecked():
                    self._send_vmd_cmd("colorscalebar 1.4")
            else:
                self._log("VMD socket not available — start VMD with `vmd -e surfanalysis.vmd`")
        except Exception as e:
            self._log(f"VMD launch error: {e}")

    # ── Render Current View ──
    def _action_render_view(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save rendered image", "", "TGA (*.tga);;PNG (*.png)")
        if not path:
            return
        self._render_tachyon(path)

    # ── Batch Render ──
    def _action_render(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save rendered image", "", "TGA (*.tga);;PNG (*.png)")
        if not path:
            return
        self._render_tachyon(path)

    def _render_tachyon(self, output_path):
        res = self.res_combo.currentText()
        nthreads = self.nthreads_spin.value()
        self._log(f"Rendering: {output_path} ({res})")
        self._send_vmd_cmd(f'render TachyonInternal "{output_path}"')

        # Tachyon post-processing
        tachyon_exe = self.config.get("tachyon", DEFAULT_TACHYON)
        tga_path = output_path if output_path.endswith('.tga') else output_path.rsplit('.', 1)[0] + '.tga'

        try:
            if os.path.exists(tachyon_exe) and os.path.exists(tga_path):
                subprocess.run([tachyon_exe, tga_path, "-format", "TARGA",
                                "-numthreads", str(nthreads)], timeout=60)
        except Exception as e:
            self._log(f"Tachyon error: {e}")

        self._log(f"Render complete: {output_path}")
        InfoBar.success(
            title='Done', content=f'Saved to {output_path}',
            orient=Qt.Horizontal, isClosable=True, duration=3000,
            position=InfoBarPosition.TOP_RIGHT, parent=self
        )

    # ── Pick Extrema ──
    def _action_pick(self):
        if self._current_mode not in ("ext", "all"):
            self._log("Pick mode only works in EXT or ALL mode")
            return
        self._log("Entering pick mode — click atoms in VMD")
        self._send_vmd_cmd(
            "trace add variable ::vmd_pick_event write _esp_pick_cb\n"
            "proc _esp_pick_cb {args} {\n"
            "    global vmd_pick_atom vmd_pick_mol\n"
            "    set mol $vmd_pick_mol\n"
            "    set atom_idx $vmd_pick_atom\n"
            "}\n"
        )

    # ── Area Chart ──
    def _action_area_chart(self):
        if not self._files:
            InfoBar.warning(
                title='No files', content='Please add .fch files first.',
                orient=Qt.Horizontal, isClosable=True, duration=3000,
                position=InfoBarPosition.TOP_RIGHT, parent=self
            )
            return
        if not _HAS_MPL:
            InfoBar.error(
                title='Missing dependency', content='matplotlib is required for area chart.',
                orient=Qt.Horizontal, isClosable=True, duration=3000,
                position=InfoBarPosition.TOP_RIGHT, parent=self
            )
            return

        mwfn_exe = self.config.get("multiwfn", DEFAULT_MULTIWFN)
        clow = float(self.clow_edit.text() or "-50")
        chigh = float(self.chigh_edit.text() or "50")

        self._set_busy(True)
        self._set_status("Analyzing ESP area...")
        self._area_worker = AreaAnalysisWorker(self._files, mwfn_exe, clow, chigh)
        self._area_worker.log_signal.connect(self._log)
        self._area_worker.progress_signal.connect(self._set_progress)
        self._area_worker.data_signal.connect(self._on_area_data_ready)
        self._area_worker.start()

    def _on_area_data_ready(self, all_data):
        self._set_busy(False)
        self._set_status("Ready")
        if all_data:
            dlg = AreaChartDialog(all_data, self)
            dlg.exec_()
        else:
            InfoBar.error(
                title='Error', content='No area data extracted.',
                orient=Qt.Horizontal, isClosable=True, duration=3000,
                position=InfoBarPosition.TOP_RIGHT, parent=self
            )

    def _action_stop(self):
        self._log("Stop requested")
        if self._mwfn_worker and self._mwfn_worker.isRunning():
            self._mwfn_worker.terminate()
        if self._area_worker and self._area_worker.isRunning():
            self._area_worker.terminate()
        self._set_busy(False)
        self._set_status("Stopped")


# ═══════════════════════════════════════════════════════════════
#  Path Settings Page
# ═══════════════════════════════════════════════════════════════

class PathPage(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        card = CardWidget(self)
        gl = QVBoxLayout(card)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.addWidget(SubtitleLabel("Path Settings"))

        # Multiwfn
        mwfn_row = QHBoxLayout()
        mwfn_row.addWidget(BodyLabel("Multiwfn:"))
        self.mwfn_edit = LineEdit()
        self.mwfn_edit.setText(self.config.get("multiwfn", DEFAULT_MULTIWFN))
        mwfn_row.addWidget(self.mwfn_edit, stretch=1)
        btn_mwfn = PushButton("Browse")
        btn_mwfn.clicked.connect(lambda: self._browse_exe(self.mwfn_edit, "Multiwfn.exe"))
        mwfn_row.addWidget(btn_mwfn)
        gl.addLayout(mwfn_row)

        # VMD
        vmd_row = QHBoxLayout()
        vmd_row.addWidget(BodyLabel("VMD:"))
        self.vmd_edit = LineEdit()
        self.vmd_edit.setText(self.config.get("vmd", DEFAULT_VMD))
        vmd_row.addWidget(self.vmd_edit, stretch=1)
        btn_vmd = PushButton("Browse")
        btn_vmd.clicked.connect(lambda: self._browse_exe(self.vmd_edit, "vmd.exe"))
        vmd_row.addWidget(btn_vmd)
        gl.addLayout(vmd_row)

        # VMD Dir
        vmdir_row = QHBoxLayout()
        vmdir_row.addWidget(BodyLabel("VMD Dir:"))
        self.vmdir_edit = LineEdit()
        self.vmdir_edit.setText(self.config.get("vmd_dir", ""))
        vmdir_row.addWidget(self.vmdir_edit, stretch=1)
        btn_vmdir = PushButton("Browse")
        btn_vmdir.clicked.connect(lambda: self._browse_dir(self.vmdir_edit))
        vmdir_row.addWidget(btn_vmdir)
        gl.addLayout(vmdir_row)

        # Tachyon
        t_row = QHBoxLayout()
        t_row.addWidget(BodyLabel("Tachyon:"))
        self.tachyon_edit = LineEdit()
        self.tachyon_edit.setText(self.config.get("tachyon", DEFAULT_TACHYON))
        t_row.addWidget(self.tachyon_edit, stretch=1)
        btn_t = PushButton("Browse")
        btn_t.clicked.connect(lambda: self._browse_exe(self.tachyon_edit, "tachyon_WIN32.exe"))
        t_row.addWidget(btn_t)
        gl.addLayout(t_row)

        self._btn_save = PrimaryPushButton(FluentIcon.SAVE, "Save Configuration")
        self._btn_save.clicked.connect(self._save)
        gl.addWidget(self._btn_save)

        layout.addWidget(card)
        layout.addStretch()

    def _browse_exe(self, edit, name):
        p, _ = QFileDialog.getOpenFileName(self, f"Select {name}", "", "EXE (*.exe);;All (*)")
        if p:
            edit.setText(p)

    def _browse_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Select VMD working directory")
        if d:
            edit.setText(d)

    def _save(self):
        cfg = configparser.ConfigParser()
        cfg['Paths'] = {
            'multiwfn': self.mwfn_edit.text(),
            'vmd': self.vmd_edit.text(),
            'vmd_dir': self.vmdir_edit.text(),
            'tachyon': self.tachyon_edit.text(),
        }
        with open(CONFIG_FILE, 'w') as f:
            cfg.write(f)
        InfoBar.success(
            title='Saved', content='Configuration saved.',
            orient=Qt.Horizontal, isClosable=True, duration=2000,
            position=InfoBarPosition.TOP_RIGHT, parent=self
        )


# ═══════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        setTheme(Theme.AUTO)
        setThemeColor('#1976D2')

        self.config = self._load_config()

        self.analysis_page = AnalysisPage(self.config, self)
        self.analysis_page.setObjectName("analysisPage")
        self.path_page = PathPage(self.config, self)
        self.path_page.setObjectName("pathPage")

        self.addSubInterface(self.analysis_page, FluentIcon.HOME, "Analysis & Plot")
        self.addSubInterface(self.path_page, FluentIcon.SETTING, "Path Settings",
                             position=NavigationItemPosition.BOTTOM)

        self.setWindowTitle("ESPViewer")
        self.resize(1000, 900)
        self.setMinimumSize(900, 700)

    def _load_config(self):
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            cp = configparser.ConfigParser()
            cp.read(CONFIG_FILE)
            if 'Paths' in cp:
                cfg = dict(cp['Paths'])
        if 'multiwfn' not in cfg:
            cfg['multiwfn'] = DEFAULT_MULTIWFN
        if 'vmd' not in cfg:
            cfg['vmd'] = DEFAULT_VMD
        if 'tachyon' not in cfg:
            cfg['tachyon'] = DEFAULT_TACHYON
        if 'vmd_dir' not in cfg:
            cfg['vmd_dir'] = ''
        return cfg


def main():
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
