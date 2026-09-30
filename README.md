# Relevance-Searcher MCP Server

将相关性搜索引擎封装为 **MCP 服务器**，通过 stdio 协议与 MCP 客户端通信。**零外部依赖**——所有第三方包已在 `_vendored/` 内嵌，且**嵌入便携 CPython 3.12**（`runtime/`），无需本机预装 Python。

## 目录结构

```
mcp-server-dev/
├── server.py           # MCP 入口：引导解释器 → 注册 4 个工具 → 启动 stdio（薄壳）
├── bootstrap.py        # 解释器引导：_vendored / pywin32 注入 sys.path（必须先于三方导入）
├── config.py           # 路径与运行参数（os.environ 读取点唯一化）
├── tools.py            # 工具纯逻辑：参数规整 → 检索调用 → 渲染（不依赖 FastMCP）
├── webpage.py          # 网页正文抓取（解码回退 + 正文提取，纯函数可单测）
├── searxng_runtime.py  # 本地 SearXNG 生命周期（就绪探测 / 自动拉起）
├── searxng_client.py   # SearXNG JSON API 客户端
├── client_config.py    # mcp-clients.json 生成与幂等回写
├── pipeline.py         # 检索管线编排（WebSearcher + searcher 单例）
├── textutil.py         # 分词 / 查询归一 / 日期抽取 / 去重 / 结果富化
├── filters.py          # 域名黑名单、词典/天气等参考类站点守卫、质量预过滤
├── scoring.py          # 查询-结果相关性评分
├── formatting.py       # 结果渲染（LLM 上下文文本 / JSON）
├── searcher.py         # 兼容外观层：旧导入路径仍可用，实现已拆到上述模块
├── __init__.py         # 包定义
├── __main__.py         # python -m 启动入口
├── run-server.cmd      # 便携启动器（首选 runtime 解释器，缺失则回退 PATH）
├── tests/              # test_units.py（离线单测）+ test_searcher_regression.py（联网回归）
├── scripts/            # 依赖重建与 SearXNG overlay 应用脚本
├── overlays/searxng/   # 本项目对 SearXNG 的定制（settings.yml + 引擎补丁）
├── _vendored/          # 内嵌第三方依赖（不入库，可由锁文件重建）
├── runtime/            # 便携 CPython 3.12 embeddable（不入库，可重建）
├── semantic-relevance/ # submodule：上游语义相关性库
├── SearXNGforWindows/  # submodule：便携 SearXNG（本地定制见 overlays/）
└── mcp-clients.json    # 跨客户端复用的服务注册定义（生成物，不入库）
```

SearXNG 实例位于 `SearXNGforWindows/` 目录（便携版 Python 3.11 环境，含 300+ 引擎配置）。

## 快速启动

**无需本机安装任何依赖**。运行前请确认 SearXNG 可用，或由 `server.py` 自动拉起：

```bash
cd mcp-server-dev
python server.py
```

推荐通过便携启动器 `run-server.cmd` 启动（自动解析项目根、首选 `runtime\python.exe`）：

```cmd
run-server.cmd
```

或指定 SearXNG 地址（可选，不配则自动降级到 DuckDuckGo）：

```bash
SEARXNG_URL=http://127.0.0.1:8888 python server.py
```

## 环境变量

| 变量                     | 默认值                     | 说明                        |
| ---------------------- | ----------------------- | ------------------------- |
| `SEARXNG_URL`          | `http://127.0.0.1:8888` | 本地 SearXNG 实例地址             |
| `SEARXNG_TIMEOUT`      | `15`                    | 搜索超时秒数                     |
| `SEARXNG_AUTOSTART`    | `1`                     | MCP 启动时是否自动拉起 SearXNG（`0` 关闭） |
| `SEARXNG_READY_TIMEOUT`| `60`                    | 等待 SearXNG 就绪秒数             |

不配置 SearXNG 时自动降级到 DuckDuckGo（CN 网络下仅 yandex 后端可用）。

## MCP 工具

### 1. `web_search`

单组搜索，返回格式化 Markdown 文本（可直接注入 LLM 上下文）。

| 参数            | 类型     | 默认值       | 说明                                |
| ------------- | ------ | --------- | --------------------------------- |
| `query`       | string | —         | 搜索关键词（必填）                         |
| `max_results` | int    | 8         | 返回结果数（1-15）                       |
| `region`      | string | `cn-zh`   | 区域：`cn-zh` / `us-en` / `wt-wt`      |
| `category`    | string | `general` | 类别：`general`（普通网页）/ `science`（学术搜索） |

### 2. `search_multi`

并行多组搜索，返回多组格式化结果。

| 参数            | 类型       | 默认值     | 说明            |
| ------------- | -------- | ------- | ------------- |
| `queries`     | string[] | —       | 搜索关键词列表（必填）   |
| `max_results` | int      | 5       | 每组返回结果数（1-10） |
| `region`      | string   | `cn-zh` | 区域            |

### 3. `search_format`

搜索并以 JSON 格式返回原始结果（`[{title, url, snippet}]`），供程序化使用。

参数与 `web_search` 相同。

## 搜索降级链与路由

```
SearXNG（本地元搜索） → DuckDuckGo（yandex 后端） → 空结果
```

按查询类型路由引擎组：

| 场景            | 引擎组                                                        | 说明                          |
| ------------- | ---------------------------------------------------------- | --------------------------- |
| 学术 `science` | `crossref,semantic_scholar,openalex,pubmed,core,base,unpaywall` + `bing` | baidu/sogou 无 science 类别     |
| 中文 general   | `baidu,sogou` + `bing`                                        | quark 因 X5SEC 反爬被排除          |
| 英文 general   | `bing,yandex,searx`                                          | 语言错配惩罚 + 停用词保底过滤             |

## 过滤管线

```
SearXNG 多引擎并行 → URL 去重 → 域名黑名单 → 文本质量预过滤 → 相关性评分排序 → 截断输出
```

- **域名黑名单**：屏蔽 30+ 低质域名（游戏下载、字典爬虫、SEO 导航等）。
- **文本质量预过滤**（借鉴 opc_data_filtering + 中文信号）：乱码、低熵重复、n-gram 重复、链接堆砌、格式异常、SEO 密度、关键词堆砌等，中英文阈值分开。
- **相关性评分**（参考 [semantic-relevance](https://github.com/m4n1shg/semantic-relevance)）：标题 55% + 摘要 35% + URL 10%，含内容类型不匹配扣分与保底分；general 门槛 0.12。
- **学术排序**（`_rank_academic`）：学术源域名 +0.28、教程站 −0.20，双门槛（学术 ≥0.06 / 非学术 ≥0.16）。

## 嵌入 Python（runtime/）

项目自带便携 CPython 3.12（官方 embeddable 版），解压于 `runtime/python/`。

- **为什么嵌入**：`_vendored/` 内第三方依赖是 **cp312 构建**，用系统 Python 时版本不匹配会导致加载失败；嵌入后可保证任意机器零安装即可运行。
- **版本守卫**：`run-server.cmd` 校验解释器为 CPython 3.12，否则告警。
- **注意**：embeddable 版 `python312._pth` 默认不启用 `site`，因此 **必须** 依赖 `server.py` 手动把 `_vendored/` 与 pywin32 子路径插入 `sys.path`（含 DLL 目录 bootstrap）。请勿删除该逻辑。

## run-server.cmd 便携启动器

`run-server.cmd` 为 MCP 客户端推荐入口，具备以下特性：

- 用 `%~dp0` 解析项目根目录 → **与 CWD 无关、可移植**。
- **Python 解析顺序**：`runtime\python.exe`（项目内嵌）→ 系统 PATH 上的 python。
- **版本守卫**：非 CPython 3.12 时输出告警（cp312 依赖要求）。
- **铁律**：脚本**绝不写 stdout**（破坏 MCP stdio 协议），所有提示/错误走 stderr。

## 注册到 MCP 客户端

### 推荐（便携启动器）

```json
{
  "mcpServers": {
    "Relevance-Searcher": {
      "type": "stdio",
      "command": "cmd.exe",
      "args": ["/c", "d:\\mcp-server-dev\\run-server.cmd"],
      "env": {
        "SEARXNG_URL": "http://127.0.0.1:8888"
      }
    }
  }
}
```

### 任何 MCP 兼容客户端（stdio）

```bash
python d:\mcp-server-dev\server.py
```

> 参考 `mcp-clients.json`（根目录）获取跨客户端复用的标准定义。目录移动时仅需修改 `args` 中的启动器路径。

## 工作原理

- `server.py` 启动时：
  1. `bootstrap.ensure_sys_path()` 把 `_vendored/` 及 pywin32 子路径插入 `sys.path`（零依赖运行的前提，必须先于任何三方导入）。
  2. `searxng_runtime.ensure_running()` 探测并拉起本地 SearXNG（`/healthz` 可达即跳过）。
  3. 以 stdio 模式运行 FastMCP，注册 `web_search` / `search_multi` / `search_format` / `fetch_page` 四个工具。
     `web_search` 额外支持 `engines` / `time_range` / `dedup` / `rewrite` / `sort_by`，默认值与旧行为一致。
- `pipeline.py` 实现多引擎并行搜索 + 三级过滤管线 + 学术排序；`tools.py` 只做参数规整与渲染，`searcher.py` 保留为兼容外观层。
- SearXNG 使用捆绑 Python **3.11**（与 `_vendored` 的 3.12 不同），两者解释器分离，勿混用。

## 更多资料

- `tests/test_searcher_regression.py` — 回归基准：过滤管线断言 + 4 组真实查询（词典类/天气类结果与同题重复必须为 0）。
- `scripts/` — `bootstrap-vendor.ps1`（重建 `_vendored`）、`bootstrap-runtime.ps1`（重建便携 Python）、`apply-searxng-overlay.ps1`（把 SearXNG 定制应用回 submodule）。
- `overlays/searxng/` — 本项目对 SearXNG 的全部定制（`config/settings.yml` + 引擎补丁），上游更新不会覆盖它。

## 版本管理与依赖重建

本仓库只追踪「属于本项目的东西」：第三方依赖要么用 submodule 钉住上游 commit，要么用脚本离线重建。

| 路径 | 处理方式 |
|------|----------|
| `searcher.py` / `server.py` / `tests/` / `scripts/` / `run-server.cmd` | 入库 |
| `semantic-relevance/` | submodule（上游 `M4n1shG/semantic-relevance`） |
| `SearXNGforWindows/` | submodule（上游 `mbaozi/SearXNGforWindows`），本地定制放在 `overlays/searxng/` |
| `_vendored/` | 不入库，用 `requirements-vendor.txt` + `scripts/bootstrap-vendor.ps1` 重建 |
| `runtime/` | 不入库，用 `scripts/bootstrap-runtime.ps1` 重建（缺失时 `run-server.cmd` 回退 PATH 上的 python 3.12） |
| `mcp-clients.json` / `__pycache__/` / `*.log` / `*.bak` | 生成物，已在 `.gitignore` 中排除 |

> 为什么 SearXNG 的定制不放在 submodule 里：submodule 只记录上游 commit 指针，在它内部做的修改属于「游离的脏工作区」，不会被父仓库记录。因此本仓库把定制抽成 overlay（整份 `settings.yml` + 引擎补丁），部署后用幂等脚本重新应用。

首次克隆后：

```powershell
git submodule update --init --recursive
pwsh -File scripts/apply-searxng-overlay.ps1   # 把 SearXNG 定制应用回 submodule（幂等）
pwsh -File scripts/bootstrap-vendor.ps1        # 可选：重建 _vendored（42 个包）
pwsh -File scripts/bootstrap-runtime.ps1       # 可选：重建便携 CPython 3.12.3
python tests/test_searcher_regression.py       # 回归基准，必须全绿再提交
```

## 提交前门（Git 钩子）

`hooks/` 通过 `core.hooksPath` 生效（随仓库版本化，不往 `.git/hooks` 里拷文件）：

| 钩子 | 做什么 | 跳过方式 |
|------|--------|----------|
| `hooks/pre-commit` | 跑 `tests/test_units.py`（32 条离线断言，约 0.02s、零网络），失败即阻止提交 | `git commit --no-verify` 或 `SKIP_UNIT_TESTS=1 git commit ...` |
| `hooks/pre-push` | 跑 `scripts/check_entry.py`：确认 `server` 可导入且仍注册 4 个工具，防止拆分/改名把入口弄断 | `git push --no-verify` 或 `SKIP_IMPORT_CHECK=1 git push` |

安装（一次即可，仅影响本仓库）：

```powershell
pwsh -File scripts/install-git-hooks.ps1              # 等价于 git config core.hooksPath hooks
pwsh -File scripts/install-git-hooks.ps1 -Uninstall    # 卸载，回到 .git/hooks
```

需要联网的 `tests/test_searcher_regression.py` **不进钩子**，手动运行即可 —— 它的耗时可随网络波动，塞进提交路径只会逼人天天 `--no-verify`。
调试钩子时可用 `UNIT_TEST_FILE=...` 指向别的测试文件。
