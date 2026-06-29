# ESPViewer v2.0

An electrostatic potential (ESP) surface visualization tool based on Multiwfn + VMD with a PyQt5 GUI.

📺 **Video Demo**: [Bilibili BV1zSVW6PEwe](https://www.bilibili.com/video/BV1zSVW6PEwe/)

> **v2.0 Major Update**: Fixed rendering bugs + Color scale bar + Bilingual UI + Multithreaded processing!

---

## 📖 Required Citations

**If you use this software in your research, in addition to citing ESPViewer itself, please cite the following core works:**

> 💡 **One-click EndNote Import**: Download [`references.ris`](https://cnb.cool/chem311/ESPViewer/-/blob/master/references.ris) from this repository, then `File → Import → Import Option: RIS` in EndNote to import all references below.

### 1. Efficient ESP Evaluation Method
> Jun Zhang, Tian Lu, *Efficient Evaluation of Electrostatic Potential with Computerized Optimized Code*, Phys. Chem. Chem. Phys., **2021**, 23(36), 20323–20328. DOI: [10.1039/d1cp02805g](https://doi.org/10.1039/d1cp02805g)

### 2. Molecular Surface Quantitative Analysis (Methodological Foundation)
> Tian Lu, Feiwu Chen, *Quantitative Analysis of Molecular Surface Based on Improved Marching Tetrahedra Algorithm*, J. Mol. Graph. Model., **2012**, 38, 314–323. DOI: [10.1016/j.jmgm.2012.07.004](https://doi.org/10.1016/j.jmgm.2012.07.004)

### 3. ESP Distribution Histogram — First Proposed
> Sergio Manzetti, Tian Lu, *The Geometry and Electronic Structure of Aristolochic Acid: Possible Implications for a Frozen Resonance*, J. Phys. Org. Chem., **2013**, 26(6), 473–483. DOI: [10.1002/poc.3111](https://doi.org/10.1002/poc.3111)

### 4. ESP Distribution Histogram — First Used
> Tian Lu, Sergio Manzetti, *Wavefunction and Reactivity Study of Benzo[a]pyrene Diol Epoxide and Its Enantiomeric Forms*, Struct. Chem., **2014**, 25(5), 1521–1533. DOI: [10.1007/s11224-014-0430-6](https://doi.org/10.1007/s11224-014-0430-6)

**The ESP distribution histogram method** was first proposed and used in papers 3 and 4 above. If you use such plots in your publications, please cite these two papers in addition to the original Multiwfn papers (J. Comput. Chem. 2012 / J. Chem. Phys. 2024).

---

## Features

### Four Analysis Modes

| Mode | Description |
|------|-------------|
| **PT** (Vertex-colored) | Molecular surface vertices colored by ESP values, BWR color scale |
| **ISO** (Isosurface) | ρ=0.001 electron density isosurface, EdgyGlass semi-transparent material |
| **EXT** (Extrema) | Display ESP extrema points (maxima/minima) on the vdW surface |
| **ALL** (Overlay) | ISO + EXT combined, with extrema points labeled on the isosurface |

### Interactive VMD Preview

- Real-time 3D molecular surface rendering with free rotation/zoom/pan
- VMD TCP socket real-time control (color range, opacity adjustments take effect instantly)
- Color range changes **immediately refresh** in VMD — no need to restart preview

### Color Scale Bar 🆕

- VMD built-in `ColorScaleBar` with clear ESP (kcal/mol) labels on the left
- 10 tick marks, linked in real-time to the color range
- One-click toggle; available in both preview and rendered output

### Tachyon Dual-Path Rendering 🔧

| Path | Description |
|------|-------------|
| **Headless Mode** (One-click) | VMD text mode + `TachyonInternal`, outputs TGA automatically after Multiwfn computation, no VMD window needed |
| **Interactive Mode** (Adjust then render) | Fine-tune view in VMD preview, then call external `tachyon_WIN32.exe`, up to 4000×3000 resolution, multi-threaded |

Two independent paths, completely fixing v1.x render failures.

### Multi-level Resolution 🆕

| Level | Resolution | Use Case |
|-------|-----------|----------|
| Low | 1200×900 | Quick preview |
| Medium | 2000×1500 | Regular publication |
| High | 3000×2250 | High-quality output |
| Ultra | 4000×3000 | Publication-grade quality |

### Interactive Extremum Query 🆕

- Click extremum point spheres in VMD preview to query ESP values in real-time
- Extremum label toggle (font size and offset adjustable)
- Real-time font size and offset distance adjustments

### ESP Area Distribution Chart 🆕

- Surface area distribution histogram + line chart, interactive matplotlib window
- Editable chart title and axis labels
- Two Y-axis modes: area (Å²) and percentage
- Customizable colormap and legend toggle
- Data export: `_esp_area_data.txt` (tab-separated, importable by Origin/Excel)

### ESP Range Auto-Detection

- Automatically runs Multiwfn module 12 to query global ESP surface extrema
- Auto-fills color range into UI, reducing manual input

### Batch Processing

- Supports single/multiple .fch/.fchk file selection
- Supports importing an entire folder
- Unlimited batch file count, auto-numbered systems
- Progress bar reflects multi-step progress in real-time

### Bilingual UI 🆕

- Built-in full Chinese/English interface切换, no extra language pack needed
- One-click toggle button; all UI text and logs update instantly

### Other Features

- Real-time opacity slider adjustment (ISO/ALL modes)
- Multiwfn parallel thread count setting (1–64)
- Multi-threaded Tachyon rendering
- Path configuration auto-saved to `esp_surface_gui.ini`
- Automatic temporary directory cleanup
- Background QThread multithreading: UI stays responsive

---

## Dependencies

### Required (External Programs)

| Tool | Purpose | Download |
|------|---------|----------|
| [Multiwfn](http://sobereva.com/multiwfn/) | Compute ESP surface data from .fchk | [Multiwfn Download](http://sobereva.com/multiwfn/) |
| [VMD](https://www.ks.uiuc.edu/Research/vmd/) | 3D molecular visualization + built-in Tachyon renderer | [VMD Download](https://www.ks.uiuc.edu/Development/Download/download.cgi?PackageName=VMD) |
| Tachyon (bundled with VMD) | External ray-tracing renderer (needed for interactive mode) | `tachyon_WIN32.exe` in VMD directory |

### Python Packages

```bash
pip install PyQt5 matplotlib numpy pillow
```

| Package | Purpose | Required |
|---------|---------|----------|
| `PyQt5` | GUI framework | ✅ |
| `matplotlib` | ESP area distribution chart | Optional (chart disabled without it) |
| `numpy` | Numerical computation (chart dependency) | Optional |
| `Pillow` | BMP → PNG image conversion (render dependency) | Optional (outputs BMP without it) |

---

## Project Structure

```
ESPViewer2/
├── esp_surface_gui.py          # ★ v2.0 main program (PyQt5, bilingual, full-featured)
├── esp_surface_gui_fluent.py   # v2.0 Fluent UI style variant
├── ESPViewer.py                # v1.x basic version (single-panel layout)
├── ESPViewer_cn.py             # v1.x Chinese enhanced version
├── ESPViewer_en.py             # v1.x English enhanced version
├── ESPViewer.spec              # PyInstaller packaging config
├── ESPViewer2.spec             # PyInstaller v2.0 packaging config
├── ESPViewer-Fluent.spec       # PyInstaller Fluent packaging config
├── espviewer.ico               # Application icon
├── CITATION.cff                # Citation metadata
├── README.md                   # This file
├── references.ris              # EndNote-compatible citation file
├── test_esp_area.csv           # Area distribution test data
├── test_esp_area.png           # Area distribution test image
├── esp_area_all.csv            # Area distribution sample data
├── progress_bar_demo.py        # Progress bar demo
└── 公众号推文_ESPViewer_v2.0.md # v2.0 release announcement
```

### Main Classes in esp_surface_gui.py

| Class | Description |
|-------|-------------|
| `ESPSurfaceGUI` (QMainWindow) | Main window with tab switching (Analysis / Path Settings) |
| `MultiwfnWorker` (QThread) | Background thread: runs Multiwfn computation + launches VMD |
| `AreaAnalysisWorker` (QThread) | Background thread: ESP area distribution computation |
| `AreaChartDialog` (QDialog) | Interactive matplotlib chart dialog |
| `RenderViewWorker` (QThread) | Background thread: call external Tachyon to render current view |
| `StatusButton` (QPushButton) | Capsule status button with breathing animation when running |

### Key Functions

| Function | Description |
|----------|-------------|
| `run_multiwfn()` | Call Multiwfn with command file redirection, real-time stdout reading |
| `run_multiwfn_area_dist()` | Run ESP area distribution analysis |
| `generate_vmd_script_pt/iso/ext/all()` | Generate VMD Tcl scripts for four modes |
| `render_current_view_tachyon()` | Render current VMD view via Tachyon ray-tracing |
| `send_vmd_cmd()` | Send Tcl commands to VMD via TCP socket |
| `_parse_esp_range_from_stdout()` | Parse ESP extrema range from Multiwfn stdout |

---

## Workflow

```
                      ┌──────────────────────────┐
                      │  1. Select .fch files     │
                      │  2. Choose analysis mode  │
                      │  3. Set color range/params│
                      └─────────┬────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                                    ▼
   ┌──────────────────────┐           ┌──────────────────────┐
   │  [Preview]            │           │  [One-Click Render]  │
   │  Multiwfn computation │           │  Multiwfn computation│
   │  → Generate VMD Tcl   │           │  → Generate VMD Tcl  │
   │  → Launch VMD window  │           │  → VMD text mode     │
   │  → TCP socket control │           │  → TachyonInternal   │
   └──────────┬───────────┘           │  → Output TGA        │
              │                       └──────────────────────┘
              ▼
   ┌──────────────────────┐
   │  Interactive ops:     │
   │  - Rotate/Zoom/Pan    │
   │  - Color range adjust │
   │  - Opacity adjust     │
   │  - Extremum query     │
   │  - [Render View]      │
   └──────────────────────┘
```

---

## Usage

### Run from Source

```bash
# Install dependencies
pip install PyQt5 matplotlib numpy pillow

# Run v2.0 main program
python esp_surface_gui.py
```

### Run v1.x from Source

```bash
python ESPViewer_cn.py    # Chinese version
python ESPViewer_en.py    # English version
```

### Package as exe

```bash
pip install pyinstaller
pyinstaller ESPViewer.spec    # v1.x packaging
pyinstaller ESPViewer2.spec   # v2.0 packaging
```

### Download Pre-built Executable

📦 Download pre-built exe from the Release page: [V2.0 Release](https://cnb.cool/chem311/ESPViewer/-/releases/tag/V2.0)

- `ESPViewer_CN.exe` — Chinese enhanced version
- `ESPViewer_EN.exe` — English enhanced version

No Python installation required; download and run.

---

## Input / Output

### Input Files

Supports Gaussian formatted checkpoint files: `.fch` / `.fchk`

### Output Files

| File | Description |
|------|-------------|
| `*_esp.tga` / `*_esp.png` | Tachyon rendered image |
| `*_esp_area.png` | ESP area distribution histogram |
| `*_esp_area_data.txt` | Tab-separated area data (Origin/Excel importable) |

---

## Path Configuration

On first run, configure the following paths:

| Setting | Description | Example |
|---------|-------------|---------|
| Multiwfn | Path to Multiwfn.exe | `E:\Multiwfn_2026\Multiwfn.exe` |
| VMD exe | Path to vmd.exe | `C:\Program Files\VMD\vmd.exe` |
| VMD Dir | VMD working directory | `C:\Program Files\VMD` |
| Tachyon | Path to tachyon_WIN32.exe | `C:\Program Files\VMD\tachyon_WIN32.exe` |

Configuration is auto-saved to `esp_surface_gui.ini` next to the program.

---

## Acknowledgments

The core computation engine of this software comes from **Multiwfn**, developed by **Prof. Tian Lu**. Our sincerest gratitude!

> Tian Lu, Feiwu Chen, *Multiwfn: A Multifunctional Wavefunction Analyzer*, J. Comput. Chem., **2012**, 33, 580–592. DOI: [10.1002/jcc.22885](https://doi.org/10.1002/jcc.22885)
>
> Tian Lu, *A Comprehensive Electron Wavefunction Analysis Toolbox for Chemists, Multiwfn*, J. Chem. Phys., **2024**, 161, 082503. DOI: [10.1063/5.0216272](https://doi.org/10.1063/5.0216272) (JCP Editors' Choice 2024)

Multiwfn has been cited in over 40,000 publications by users in 90+ countries.

---

## 📖 Cite

If you use this software in your research, please cite it as follows:

> Hou, C. (2026). *ESPViewer* (Version 1.0.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.20791148

```bibtex
@software{espviewer_2026,
  author       = {Hou, Cheng},
  title        = {ESPViewer},
  year         = 2026,
  publisher    = {Zenodo},
  version      = {1.0.0},
  doi          = {10.5281/zenodo.20791148},
  url          = {https://doi.org/10.5281/zenodo.20791148}
}
```

---

## License

MIT License

---

---

# ESPViewer v2.0（中文）

基于 Multiwfn + VMD 的静电势（ESP）表面可视化工具，PyQt5 GUI 界面。

📺 **视频演示**：[B站 BV1zSVW6PEwe](https://www.bilibili.com/video/BV1zSVW6PEwe/)

> **v2.0 重磅更新**：修复渲染出图 bug + 新增色彩刻度轴 + 中英双语 + 多线程后台处理！

---

## 📖 必引文献 / Required Citations

**如果您的研究使用了本软件，除引用 ESPViewer 外，还请务必引用以下核心工作：**

> 💡 **一键导入 EndNote**：下载仓库中的 [`references.ris`](https://cnb.cool/chem311/ESPViewer/-/blob/master/references.ris)，EndNote 菜单 `File → Import → Import Option: RIS` 即可导入全部文献。

### 1. ESP 高效计算方法
> Jun Zhang, Tian Lu, *Efficient Evaluation of Electrostatic Potential with Computerized Optimized Code*, Phys. Chem. Chem. Phys., **2021**, 23(36), 20323–20328. DOI: [10.1039/d1cp02805g](https://doi.org/10.1039/d1cp02805g)

### 2. ESP 表面分析（分子表面定量分析方法基础）
> Tian Lu, Feiwu Chen, *Quantitative Analysis of Molecular Surface Based on Improved Marching Tetrahedra Algorithm*, J. Mol. Graph. Model., **2012**, 38, 314–323. DOI: [10.1016/j.jmgm.2012.07.004](https://doi.org/10.1016/j.jmgm.2012.07.004)

### 3. 静电势分布柱形图 — 首次提出
> Sergio Manzetti, Tian Lu, *The Geometry and Electronic Structure of Aristolochic Acid: Possible Implications for a Frozen Resonance*, J. Phys. Org. Chem., **2013**, 26(6), 473–483. DOI: [10.1002/poc.3111](https://doi.org/10.1002/poc.3111)

### 4. 静电势分布柱形图 — 首次使用
> Tian Lu, Sergio Manzetti, *Wavefunction and Reactivity Study of Benzo[a]pyrene Diol Epoxide and Its Enantiomeric Forms*, Struct. Chem., **2014**, 25(5), 1521–1533. DOI: [10.1007/s11224-014-0430-6](https://doi.org/10.1007/s11224-014-0430-6)

**静电势分布柱形图统计方法**在上述第 3、4 两篇文章中首次提出和使用。如果您在文章中也使用了这种图，除引用 Multiwfn 原文（J. Comput. Chem. 2012 / J. Chem. Phys. 2024）外，请一并引用这两篇。

---

## 功能

### 四种分析模式

| 模式 | 说明 |
|------|------|
| **PT**（顶点着色） | 分子表面顶点按 ESP 值着色，BWR 色阶 |
| **ISO**（等值面） | ρ=0.001 电子密度等值面，EdgyGlass 半透明材质 |
| **EXT**（极值点） | 显示 vdW 表面上的 ESP 极值点（极大/极小） |
| **ALL**（叠加） | ISO + EXT 同时显示，等值面上标注极值点 |

### VMD 交互式预览

- 实时 3D 分子表面渲染，可自由旋转/缩放/平移
- VMD TCP socket 实时控制（色彩范围、透明度等调整即时生效）
- 色彩范围修改后 VMD 端**实时刷新**，无需重启预览

### 色彩刻度轴 🆕

- VMD 内置 `ColorScaleBar`，左侧清晰标注 ESP (kcal/mol) 数值
- 10 个分度刻度，与色彩范围实时联动
- 复选框一键开关，预览和渲染出图均支持

### Tachyon 双路径渲染 🔧

| 路径 | 说明 |
|------|------|
| **无头模式**（一键渲染） | VMD 文本模式 + `TachyonInternal`，调用 Multiwfn 计算完后自动出 TGA，无需 VMD 窗口 |
| **交互模式**（调好再出） | VMD 实时预览中调好视角后，调用外部 `tachyon_WIN32.exe`，最高 4000×3000 分辨率，支持多线程 |

两条路径互为备份，彻底修复 v1.x 出图失败问题。

### 多档分辨率 🆕

| 档位 | 分辨率 | 适用场景 |
|------|--------|----------|
| 低 | 1200×900 | 快速预览 |
| 中 | 2000×1500 | 常规论文 |
| 高 | 3000×2250 | 高清出图 |
| 超高 | 4000×3000 | 高质量出版 |

### 极值点交互查询 🆕

- VMD 预览中点击极值点球，ESP 值实时回传显示
- 极值点标注开关控制（字号、偏移量可调）
- 支持标签字体大小和偏移距离实时调整

### ESP 分区面积图 🆕

- 分面面积分布柱状图 + 折线图，matplotlib 交互窗口
- 图表标题、轴标签可自定义编辑
- 支持面积 (`Å²`) 和百分比两种 Y 轴模式
- 自定义色彩映射 (colormap) 和图例切换
- 数据导出：`_esp_area_data.txt`（Tab 分隔，Origin/Excel 可用）

### ESP 范围自动检测

- 自动运行 Multiwfn 模块 12 查询表面 ESP 全局极值
- 自动填充色彩范围到 UI，减少手动输入

### 批量处理

- 支持单选/多选 .fch/.fchk 文件
- 支持导入整个文件夹
- 批量计算时文件数无上限，多系统自动编号
- 进度条实时反映多步骤进度

### 中英双语 🆕

- 内置完整中英文界面切换，无需额外语言包
- 一键切换按钮，所有 UI 文本、日志即时更新

### 其他特性

- 透明度滑块实时调节（ISO/ALL 模式）
- Multiwfn 并行线程数设置（1~64）
- 点多线程渲染
- 路径配置自动保存到 `esp_surface_gui.ini`
- 临时工作目录自动清理
- 后台 QThread 多线程：界面不卡顿

---

## 依赖

### 必需（外部程序）

| 工具 | 用途 | 下载 |
|------|------|------|
| [Multiwfn](http://sobereva.com/multiwfn/) | 从 .fchk 计算 ESP 表面数据 | [Multiwfn 下载](http://sobereva.com/multiwfn/) |
| [VMD](https://www.ks.uiuc.edu/Research/vmd/) | 3D 分子可视化 + 内置 Tachyon 渲染 | [VMD 下载](https://www.ks.uiuc.edu/Development/Download/download.cgi?PackageName=VMD) |
| Tachyon（VMD 自带） | 外部光线追踪渲染器（交互模式出图需要） | VMD 安装目录自带 `tachyon_WIN32.exe` |

### Python 包

```bash
pip install PyQt5 matplotlib numpy pillow
```

| 包 | 用途 | 必需 |
|---|------|------|
| `PyQt5` | GUI 框架 | ✅ |
| `matplotlib` | ESP 分区面积图 | 可选（无则不启用图表） |
| `numpy` | 数组计算（图表依赖） | 可选 |
| `Pillow` | BMP → PNG 图片转换（渲染出图依赖） | 可选（无则输出 BMP） |

---

## 项目结构

```
ESPViewer2/
├── esp_surface_gui.py          # ★ v2.0 主程序（PyQt5，中英双语，全功能）
├── esp_surface_gui_fluent.py   # v2.0 Fluent UI 风格版本（备选）
├── ESPViewer.py                # v1.x 基础版（单面板布局）
├── ESPViewer_cn.py             # v1.x 中文增强版
├── ESPViewer_en.py             # v1.x 英文增强版
├── ESPViewer.spec              # PyInstaller 打包配置
├── ESPViewer2.spec             # PyInstaller v2.0 打包配置
├── ESPViewer-Fluent.spec       # PyInstaller Fluent 版打包配置
├── espviewer.ico               # 应用图标
├── CITATION.cff                # 引用信息
├── README.md                   # 本文件
├── test_esp_area.csv           # 面积分布测试数据
├── test_esp_area.png           # 面积分布测试图片
├── esp_area_all.csv            # 面积分布示例数据
├── progress_bar_demo.py        # 进度条演示
└── 公众号推文_ESPViewer_v2.0.md # v2.0 发布推文
```

### esp_surface_gui.py 主要类结构

| 类 | 说明 |
|----|------|
| `ESPSurfaceGUI` (QMainWindow) | 主窗口，含 Tab 切换（分析/路径设置） |
| `MultiwfnWorker` (QThread) | 后台线程：运行 Multiwfn 计算 + 启动 VMD |
| `AreaAnalysisWorker` (QThread) | 后台线程：ESP 分区面积分布计算 |
| `AreaChartDialog` (QDialog) | 交互式 matplotlib 图表窗口 |
| `RenderViewWorker` (QThread) | 后台线程：调用外部 Tachyon 渲染当前视角 |
| `StatusButton` (QPushButton) | 胶囊状态按钮，运行中呼吸动画 |

### 外部函数

| 函数 | 说明 |
|------|------|
| `run_multiwfn()` | 调用 Multiwfn，命令文件重定向输入，实时读取 stdout |
| `run_multiwfn_area_dist()` | 运行 ESP 面积分布分析 |
| `generate_vmd_script_pt/iso/ext/all()` | 生成四种模式的 VMD Tcl 脚本 |
| `render_current_view_tachyon()` | 通过 VMD TCP 渲染当前视角 + Tachyon 光线追踪 |
| `send_vmd_cmd()` | 通过 TCP socket 向 VMD 发送 Tcl 命令 |
| `_parse_esp_range_from_stdout()` | 从 Multiwfn stdout 解析 ESP 极值范围 |

---

## 工作流程

```
                      ┌──────────────────────┐
                      │  1. 选择 .fch 文件     │
                      │  2. 选择分析模式       │
                      │  3. 设置色彩范围/参数   │
                      └─────────┬────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                                    ▼
   ┌──────────────────────┐           ┌──────────────────────┐
   │  [预览]               │           │  [一键渲染]           │
   │  Multiwfn 计算        │           │  Multiwfn 计算        │
   │  → 生成 VMD Tcl 脚本  │           │  → 生成 VMD Tcl 脚本  │
   │  → 启动 VMD 交互窗口  │           │  → VMD 文本模式       │
   │  → TCP socket 实时控制│           │  → TachyonInternal    │
   └──────────┬───────────┘           │  → 输出 TGA           │
              │                       └──────────────────────┘
              ▼
   ┌──────────────────────┐
   │  交互操作：           │
   │  - 旋转/缩放/平移     │
   │  - 色彩范围实时调节   │
   │  - 透明度调节         │
   │  - 极值点查询         │
   │  - [调好出图] 渲染    │
   └──────────────────────┘
```

---

## 使用方法

### 从源码运行

```bash
# 安装依赖
pip install PyQt5 matplotlib numpy pillow

# 运行 v2.0 主程序
python esp_surface_gui.py
```

### 从源码运行 v1.x

```bash
python ESPViewer_cn.py    # 中文版
python ESPViewer_en.py    # 英文版
```

### 打包为 exe

```bash
pip install pyinstaller
pyinstaller ESPViewer.spec    # v1.x 打包
pyinstaller ESPViewer2.spec   # v2.0 打包
```

### 下载预打包版本

📦 打包好的 exe 请到 Release 页面下载：[V2.0 Release](https://cnb.cool/chem311/ESPViewer/-/releases/tag/V2.0)

- `ESPViewer_CN.exe` — 中文增强版
- `ESPViewer_EN.exe` — 英文增强版

无需安装 Python，下载即用。

---

## 输入输出

### 输入文件

支持 Gaussian 格式化检查点文件：`.fch` / `.fchk`

### 输出文件

| 文件 | 说明 |
|------|------|
| `*_esp.tga` / `*_esp.png` | Tachyon 渲染图片 |
| `*_esp_area.png` | ESP 分区面积柱状图 |
| `*_esp_area_data.txt` | Tab 分隔面积数据（Origin/Excel 可导入） |

---

## 路径配置

首次运行后需设置以下路径：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| Multiwfn | Multiwfn.exe 路径 | `E:\Multiwfn_2026\Multiwfn.exe` |
| VMD exe | vmd.exe 路径 | `C:\Program Files\VMD\vmd.exe` |
| VMD Dir | VMD 工作目录 | `C:\Program Files\VMD` |
| Tachyon | tachyon_WIN32.exe 路径 | `C:\Program Files\VMD\tachyon_WIN32.exe` |

配置自动保存到程序同目录下的 `esp_surface_gui.ini`。

---

## 致谢

本软件的核心计算引擎来自 **卢天老师** 开发的 **Multiwfn** 多功能波函数分析程序，特此致以最诚挚的感谢！

> Tian Lu, Feiwu Chen, *Multiwfn: A Multifunctional Wavefunction Analyzer*, J. Comput. Chem., **2012**, 33, 580–592. DOI: [10.1002/jcc.22885](https://doi.org/10.1002/jcc.22885)
>
> Tian Lu, *A Comprehensive Electron Wavefunction Analysis Toolbox for Chemists, Multiwfn*, J. Chem. Phys., **2024**, 161, 082503. DOI: [10.1063/5.0216272](https://doi.org/10.1063/5.0216272) (JCP Editors' Choice 2024)

Multiwfn 目前已被超过 4 万篇论文引用，用户遍布全球 90 余国。

---

## 📖 引用 / Cite

若您的研究使用了本软件，请按以下格式引用：

> Hou, C. (2026). *ESPViewer* (Version 1.0.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.20791148

```bibtex
@software{espviewer_2026,
  author       = {Hou, Cheng},
  title        = {ESPViewer},
  year         = 2026,
  publisher    = {Zenodo},
  version      = {1.0.0},
  doi          = {10.5281/zenodo.20791148},
  url          = {https://doi.org/10.5281/zenodo.20791148}
}
```

---

## 许可证

MIT License
