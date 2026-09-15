# 长上下文 QA · 本地产线控制台

Electron 桌面应用，用来在本机打开工作区、配置模型密钥、审核材料、启动/暂停批量生产，并在看板上跟踪每条任务。

本目录是控制层：界面与本地 API 不改写上游 `workflow/`、`scripts/` 源码，而是通过子进程调用它们。应用启动后会把仓库根目录当作 `LCQA_CODE_ROOT`，工作根目录当作 `LCQA_ROOT`。

## 能做什么

- **工作区**：打开现有「长上下文 QA」仓库，或选一个空文件夹自动搭脚手架（`materials` / `queue` / `samples` / `archive`，并复制产线脚本）。
- **生产看板**：自动挑选或自选材料包入队；控制本批条数、并发（1–10）、出题类型与本批终点；暂停 / 中断 / 继续；按状态与日期筛选；批量开始、续跑、改回排队、取消、复验。
- **材料**：按领域 → 主题包查看结构是否齐全；可选大模型内容审核（写回 `CATALOG.json` 的 `llm_audit`，不拦入队）。
- **设置**：出题 / Qwen 评测 / 判分 / 复验 / 材料审核五路独立配置模型、Base URL、API Key 与推理强度。密钥写在本机 `~/.config/lcqa-desktop/`，不进仓库。

## 架构

```
Electron 主进程
  └─ 拉起 python3 backend/server.py   →  127.0.0.1:8765（仅本机）
       ├─ REST + WebSocket /ws/run
       └─ 子进程调用工作区内 workflow / scripts/produce_one.py

渲染进程  Vue 3 + Element Plus
```

| 分层 | 路径 | 作用 |
|------|------|------|
| 主进程 | `src/main/` | 窗口、选目录、启动/清理本地 API |
| 预加载 | `src/preload/` | 暴露 `window.lcqa.pickFolder()` |
| 界面 | `src/renderer/` | 工作区 / 看板 / 材料 / 设置 |
| 本地 API | `backend/` | FastAPI sidecar；队列、跑批、材料扫描与审核 |

开发时渲染进程走 Vite（默认端口 **5179**）。生产预览加载 `out/renderer/index.html`。

## 环境要求

- macOS / Linux（Windows 上选目录可用，但跑批控制依赖 Unix 进程组，未作为主路径）
- **Node.js** 18+（Electron 35 / electron-vite）
- **Python 3.10+**（代码使用 `str \| None` 等注解），命令名为 `python3`
- 本目录必须放在长上下文 QA **仓库根目录旁边**，例如：

  ```
  长上下文QA/                 ← LCQA_CODE_ROOT（含 workflow/、scripts/）
    workflow/
    scripts/
    materials/               ← 也可作为工作区直接打开
    long-context-qa-gui/     ← 本应用
  ```

## 安装

在本目录执行：

```bash
cd long-context-qa-gui
npm install
python3 -m pip install -r backend/requirements.txt
```

Python 依赖见 `backend/requirements.txt`：`fastapi`、`uvicorn[standard]`、`pydantic`、`openai`、`certifi`。

## 启动

```bash
npm run dev
```

主进程会：

1. 若 **8765** 上已有本机 API，先结束旧进程；
2. 以仓库根为工作目录启动 `python3 backend/server.py --host 127.0.0.1 --port 8765`；
3. 等到 `/api/health` 就绪后再打开窗口。

其它脚本：

| 命令 | 说明 |
|------|------|
| `npm run build` | 用 electron-vite 编译到 `out/` |
| `npm run preview` | 预览已编译界面 |

仅调试 API（不启动窗口）时：

```bash
python3 backend/server.py --host 127.0.0.1 --port 8765
# 可选：--workspace /path/to/workspace
```

健康检查：`GET http://127.0.0.1:8765/api/health`。

## 第一次使用

1. **选择工作根目录**  
   - 现有仓库：含 `materials/` 且已有 `scripts/produce_one.py` 或 `queue/queue.json` 时，只写入 `.lcqa/workspace.json` 标记，**不覆盖材料**。  
   - 空目录：创建数据目录，并把上游 `workflow/*.py` 与 `scripts/produce_one.py`、调用脚本、`prompts/` 等复制进去。
2. **设置**里填三路产线密钥（出题 / Qwen / 判分）。复验与材料审核可不填，会回落到判分密钥。
3. 把文档放进 `materials/<domain>/<pack>/`（见下），在材料页确认结构，再到看板点「开始」。

顶部会显示密钥是否就绪，以及跑批状态（空闲 / 运行中 / 停止中 / 已中断；额度耗尽会单独标出）。

## 材料约定

按领域 → 主题包放置，软件不会替你写原文。

```
materials/<domain>/
  CATALOG.json                 # 领域索引，packs[] 列出子包
  <pack>/
    pdf/                       # 原始 PDF（优先）
    html/                      # 无 PDF 时的 HTML
    md/                        # 与源文件同 stem 的 Markdown（必填，供裁剪）
    CATALOG.json               # 本包索引
```

每个文档在 pack 的 `docs[]` 中应包含：

- `file_md`（必填）
- `file_pdf` 或 `file_html`
- `title` / `url` / `doc_id`

`status` 由产线回写（`READY` / `IN_PROGRESS` / `USED_IN_SAMPLE_NNN` / `GATE_FAILED_NNN`），不要手改源文件名。新工作区里的 `materials/example/sample-pack/` 只是空示例，放入真实材料后再生产。

材料页的「审核」会按选材标准调用大模型，结果写入 `llm_audit`，**不阻止入队**。

## 生产看板

### 本批参数

| 项 | 说明 |
|----|------|
| 本批条数 | 自动挑选时为入队上限；自选模式等于将入队的包数量 |
| 并发 | 1–10 个 worker，每条 context 只出一道候选题 |
| 出题类型 | `短答案`（默认）/ `选择题`（四选一或 2–3 项多选）/ `自动` |
| 本批终点 | `全程打包`（默认：出题 → 判分 → 消融 → 落盘）/ `出题` / `判分` |
| 优先冷源 | 自动挑选时尽量用尚未用过的冷源包 |
| 续跑技术失败 | 把先前技术失败的任务重新领出来跑 |

运行控制：**开始**、**暂停**（当前 `produce_one` 步跑完再停）、**中断**（杀掉子进程）、**继续**（跳过重新 stage，消化队列里已有任务）。OpenAI 额度耗尽会停止本批，提示充值后勾选「续跑技术失败」。

### 材料来源

- **自动挑选**：开始时按冷源优先从 `materials` stage 并入队。
- **自选材料**：勾选包；默认只计 `READY`。勾选「允许重跑已用/失败包」才会带 `--include-used`。

### 任务状态

队列常见状态：通过、已领取、运行中、排队、已出题、已判分、复检、已取消、临界 4/8、门禁失败。

单条流水线阶段：准备样本 → 题目生产 → 出题门禁 → 8× rollout → 判分 → 消融对照 → 打包交付。

难度门禁与 CLI 产线一致：`0 < avg < 0.5`（不含恰好 4/8）。恰好 4/8 进 `archive/borderline-50pct/`；`0/8` 待人工或自动复验；过易 / 全对 8/8 等不入库。

### 批量操作

对勾选任务可：开始（只跑已排队、不再挑新材料）、续跑到更后终点、改回排队、取消、复检通过（确认 0/8 为模型答错后补消融入库）、自动复验、复检不通过（从 `samples` 或待复验迁入 `archive/failed-samples` 并释放材料）。

## 工作区内目录

打开工作区后，数据都写在该目录下（打开本仓库时就是仓库根）：

| 路径 | 用途 |
|------|------|
| `materials/` | 原始材料与 CATALOG |
| `data/staging/` | stage 后的上下文窗 |
| `work/samples/` | 生产工作区（prepare / raw API） |
| `samples/` | 过门禁后的正式交付 |
| `archive/pending-review/` | 0/8 待复验 |
| `archive/borderline-50pct/` | 恰好 4/8 |
| `archive/failed-samples/` | 门禁失败 / 人工打回 |
| `archive/runs/` | 跑批快照 |
| `queue/queue.json` | 任务队列 |
| `queue/run_state.json` | 跑批状态 |
| `.lcqa/workspace.json` | 工作区标记 |

出题策略、prompt 与 CLI 批量入口仍以上游仓库为准，见仓库根的 `docs/QUESTION_STRATEGY.md` 与 `workflow/README.md`。

## 密钥与模型

配置保存在：

```
~/.config/lcqa-desktop/settings.json   # 模型、Base URL、推理强度
~/.config/lcqa-desktop/keys.env        # API Key（权限 600）
```

设置页留空 API Key 表示不改动已有值。仍可读旧文件 `~/.config/ai-keys.env`（`OPENAI_API_KEY`、`DASHSCOPE_API_KEY` 等）作为回落。

默认角色（均可在设置里改）：

| 角色 | 默认模型 | 默认推理强度 |
|------|----------|----------------|
| 出题 | `gpt-5.6-sol` | 中 |
| Qwen 评测（8× / 消融） | `qwen3.5-35b-a3b` | 高（thinking） |
| 判分 | `gpt-5.6-luna` | 中 |
| 复验 | `gpt-5.6-luna` | 中（密钥可回落判分） |
| 材料审核 | `gpt-5.6-luna` | 中（密钥可回落判分） |

判分按覆盖式：金标要点不漏、且无错误内容即可得分，不必字字对应。推理强度可选：关闭 / 低 / 中 / 高 / 极高 / 最大。

## 测试

在本目录：

```bash
python3 test_desktop.py
```

覆盖工作区脚手架、跑批控制、队列状态与部分 runner 行为。不发起真实模型请求（可用 fixture 路径的接口测离线回放）。

## 项目结构

```
long-context-qa-gui/
  src/main/          Electron 主进程
  src/preload/       contextBridge
  src/renderer/      Vue 界面
  backend/           FastAPI、跑批封装、材料扫描
  resources/         应用图标
  test_desktop.py
  package.json
```

后端以 `desktop.backend` 作为包名导入（目录也可以叫 `desktop/`）。界面偏好（最近工作区、上次标签、看板筛选等）存在本机配置目录，不进 git。
