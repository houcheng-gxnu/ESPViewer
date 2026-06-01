# ESPViewer

基于 Multiwfn + VMD 的静电势（ESP）表面可视化工具，PyQt5 GUI 界面。

📺 **视频演示**：[B站 BV1zSVW6PEwe](https://www.bilibili.com/video/BV1zSVW6PEwe/)

## 功能

- **四种分析模式**：PT（顶点着色）、ISO（等值面）、EXT（极值点）、ALL（叠加）
- **VMD 交互式预览**：实时 3D 分子表面渲染
- **Tachyon 高质量渲染**：支持无头模式直接输出图片
- **极值点交互查询**：VMD 中点击极值点球查询 ESP 值（增强版）
- **ESP 分区面积图**：分面面积分布柱状图 + 数据导出（Origin/Excel 可用）

## 依赖

### 必需（外部程序）

| 工具 | 用途 |
|------|------|
| [Multiwfn](http://sobereva.com/multiwfn/) | 从 .fchk 计算 ESP 表面数据 |
| [VMD](https://www.ks.uiuc.edu/Research/vmd/) | 3D 分子可视化 + Tachyon 渲染 |
| Tachyon（VMD 自带） | 光线追踪渲染器 |

### 可选（Python 包）

```
matplotlib  numpy  pillow  PyQt5
```

- `matplotlib + numpy`：ESP 分区面积图功能需要
- `Pillow`：BMP 到 PNG 图片转换
- `PyQt5`：GUI 框架

## 文件说明

| 文件 | 说明 |
|------|------|
| `ESPViewer.py` | 基础版（单面板布局） |
| `ESPViewer_cn.py` | 中文增强版（Tab 布局 + 极值点表格 + 透明度滑块） |
| `ESPViewer_en.py` | 英文增强版 |

## 下载

📦 **打包好的 exe 请到 Release 页面下载**：[V1.0 Release](https://cnb.cool/chem311/ESPViewer/-/releases/tag/V1.0)

- `ESPViewer_CN.exe` — 中文增强版
- `ESPViewer_EN.exe` — 英文增强版

无需安装 Python，下载即用。

## 使用方法

### 从源码运行

```bash
pip install PyQt5 matplotlib numpy pillow
python ESPViewer_cn.py
```

### 打包为 exe

```bash
pip install pyinstaller
pyinstaller ESP_Surface_GUI_CN.spec
```

## 输入文件

支持 Gaussian 格式化检查点文件：`.fch` / `.fchk`

## 输出文件

- **渲染图片**：`.png`（Tachyon 渲染结果）
- **面积分布图**：`_esp_area.png`（柱状图）
- **面积数据**：`_esp_area_data.txt`（Tab 分隔，Origin/Excel 可用）

## 路径配置

首次运行后需设置以下路径（点击浏览按钮或手动输入）：

- Multiwfn.exe
- VMD 可执行文件（vmd.exe）
- VMD 工作目录
- Tachyon 可执行文件（tachyon_WIN32.exe）

配置自动保存到 `esp_surface_gui.ini`。

## 致谢

本软件的核心计算引擎来自 **卢天老师** 开发的 **Multiwfn** 多功能波函数分析程序，特此致以最诚挚的感谢！

> Tian Lu, Feiwu Chen, *Multiwfn: A Multifunctional Wavefunction Analyzer*, J. Comput. Chem., **2012**, 33, 580–592. DOI: [10.1002/jcc.22885](https://doi.org/10.1002/jcc.22885)
>
> Tian Lu, *A Comprehensive Electron Wavefunction Analysis Toolbox for Chemists, Multiwfn*, J. Chem. Phys., **2024**, 161, 082503. DOI: [10.1063/5.0216272](https://doi.org/10.1063/5.0216272) (JCP Editors' Choice 2024)

Multiwfn 目前已被超过 4 万篇论文引用，用户遍布全球 90 余国。

## 许可证

MIT License
