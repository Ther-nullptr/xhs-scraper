# xhs-scraper

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/playwright-1.60-2EAD33.svg)](https://playwright.dev/python/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

一个**模拟人类、半自动**的[小红书 / RED](https://www.xiaohongshu.com)博主主页采集工具。
它驱动一个真实的、已登录的浏览器，像人一样**点击笔记封面、读取页面浮层**，
采集博主的**正文、高清图片和视频**，并可选地对图片做 **OCR**，把图里印着的
文字提取出来（方便后续喂给 LLM 提炼 skill 等）。

[English](README.md) | 简体中文

---

## 为什么用这种方式

小红书反爬很严。**直接用 URL 打开笔记**会撞上「扫码查看」墙——即使已登录也一样，
因为 `xsec_token` 和它的来源上下文绑定。破解之道是**表现得像真人**：

- 使用**持久化、已登录的 Chromium 用户目录**（只需扫一次码）。
- 在博主主页上**点击笔记封面**，让小红书用自己的前端路由把笔记弹成浮层
  ——不整页跳转，也就不触发那道墙。
- 从运行时的 `window.__INITIAL_STATE__` 里读取笔记数据。
- 通过**已登录的浏览器上下文 + `Referer` 头**下载图片，绕过防盗链。
- 操作之间加入随机延迟。

这是个*半*自动工具：你负责登录并导航到目标博主，剩下的交给脚本。

## 特性

- 🔓 **登录一次** —— 持久化会话，无需反复扫码。
- 🧑‍💻 **拟人操作** —— 点封面、开浮层、随机节奏。
- 🖼️ **高清原图**（含视频）以及笔记正文。
- 👥 **通用** —— 适用于*任意*博主，结果按博主分目录。
- ⏯️ **断点续传** —— 重复运行会自动跳过已下载的笔记。
- 🔤 **OCR 管线** —— 提取图片型笔记里的文字，本地离线、免费
  （[RapidOCR](https://github.com/RapidAI/RapidOCR)，中英文）。

## 环境要求

- Python 3.10+
- 能弹出浏览器窗口的桌面环境（Windows/macOS/带显示的 Linux；
  **WSL2 通过 WSLg 即可**）。
- 一个小红书账号（用于扫一次登录码）。

## 安装

```bash
git clone <你的仓库地址> xhs-scraper
cd xhs-scraper

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

Linux / WSL 还需要安装浏览器的系统依赖库：

```bash
sudo playwright install-deps chromium
```

## 使用

### 1. 采集某个博主

```bash
python scraper.py                  # 抓取当前主页的全部笔记
python scraper.py --max 5          # 只抓前 5 篇（先小范围验证）
python scraper.py --inspect        # 诊断模式：点开第一篇并 dump 数据
```

浏览器窗口弹出后：

1. **登录**（扫码，仅第一次需要）。
2. **搜索**目标博主，点进 **TA 的主页**。
3. 回到终端按 **Enter**。

脚本会识别博主，然后逐篇执行：点开封面 → 提取 → 下载 → 关闭 → 下一篇。
想抓别的博主，导航到对方主页再运行一次即可。

### 2. 从图片中提取文字（OCR）

无需浏览器，对已下载的数据完全离线运行：

```bash
python ocr.py                      # OCR output/ 下的全部内容
python ocr.py output/<博主目录>     # 只处理某个博主
python ocr.py output/<博主目录>/<某篇>  # 只处理单篇
```

## 输出结构

```
output/
└── <昵称>_<userId>/
    ├── _all_notes.md                 # 该博主所有 OCR 文本合集（方便投喂 LLM）
    └── <noteId>_<标题>/
        ├── text.md                   # 正文（标题 + 描述）
        ├── note.json                 # 提取到的原始笔记数据
        ├── text_ocr.md               # 正文 + 各图 OCR 文本
        ├── img_01.webp ...           # 高清图片
        └── video_01.mp4              # 视频（若该笔记是视频）
```

## 命令行参数

### `scraper.py`
| 参数 | 说明 |
| --- | --- |
| `--max N` | 最多抓取 N 篇（含已存在的）。`0` = 全部（默认）。 |
| `--inspect` | 诊断模式：点开第一篇并保存 `_debug_*` 文件。 |
| `--url URL` | 直接打开指定主页 URL，跳过手动导航（可选）。 |

### `ocr.py`
传入一个或多个路径（单篇目录、博主目录，或 `output/`）。不带参数时处理
`output/` 下全部。已存在的 `text_ocr.md` 会被跳过。

## 工作原理

1. `launch_persistent_context` 把 cookie 存在 `browser-data/`，登录态跨次运行保留。
2. 你按 Enter 后，脚本挑选 URL 含 `user/profile` 的标签页（小红书常在新标签打开主页）。
3. 滚动信息流，找到每个可见的 `a.cover` 并点击，笔记以浮层打开
   （`/explore/<id>?...&xsec_source=pc_user`）。
4. 读取 `window.__INITIAL_STATE__.note.noteDetailMap[id]`，提取标题、描述、
   高清图 URL 和视频流——只取标量/数组字段，规避 store 的循环引用。
5. 图片/视频通过已登录的 `context.request` + `Referer` 头抓取后落盘。
6. `ocr.py` 用 RapidOCR 逐张识别，按从上到下排序，输出按笔记/按博主的 Markdown。

## 常见问题

- **「扫码查看笔记」** —— 确认你是在*脚本弹出的浏览器窗口*里登录的（不是你平时的浏览器）。
  持久化用户目录是 `browser-data/`。
- **Linux/WSL 浏览器启动失败**（`libasound.so.2` 等）—— 运行
  `sudo playwright install-deps chromium`。
- **WSL 弹不出浏览器** —— 需要 WSLg（Windows 11 / 较新的 Windows 10）。
  确认 `echo $DISPLAY` 有值。
- **收集到 0 篇** —— 确认选定页面是博主主页（URL 含 `user/profile`）。
  用 `--inspect` 导出诊断信息。
- **选择器失效** —— 小红书前端经常改版。运行 `--inspect` 抓取当前 DOM/state，
  据此调整选择器。

## 法律与免责声明

本项目仅用于**个人学习、以及备份你有权访问的内容**。抓取行为可能违反小红书的
用户协议，且你采集的内容很可能受创作者的版权保护。

- 仅对你有权访问的内容使用。
- 控制请求频率，保持克制。
- **不要**二次传播或用于商业用途。
- 你需对使用本工具的一切后果自行负责。

作者按「现状」提供本软件，不作任何担保，也不对滥用承担任何责任。

## 许可证

[MIT](LICENSE) © 2026 ther-nullptr
