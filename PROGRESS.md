# jqcli 当前进度

更新时间：2026-08-15

## 已完成

- 已创建项目虚拟环境 `.venv`，Python 版本为 `3.13.5`。
- 已实现基础 MVP：
  - `auth status/logout/import-token/import-cookie/login`
  - `strategy ls/show/new/edit/rm`
  - `backtest run/ls/show/rm`
  - `research ls/show/download/upload/mkdir/mv/rm`
  - `research kernelspecs/kernels/sessions/exec/run`
  - `--env-file` 和 `.env` 读取
  - `--non-interactive --format json`
  - 统一错误码和 JSON 错误输出
- 已添加 `.env` 和 `.env.example`。
- `.env` 已加入 `.gitignore`，避免提交真实账号密码。
- 单元测试全量通过，最近一次结果为 `270 passed`。

## 真实聚宽登录调研

- `.env` 中的账号密码已成功登录真实聚宽。
- 登录接口已定位并接入：
  - 登录页：`/user/login/index`
  - 登录接口：`/user/login/doLogin`
  - 表单字段：
    - `CyLoginForm[username]`
    - `CyLoginForm[pwd]`
    - 登录页中的 `window.tokenData.value`
- `auth login` 已改为真实登录，成功后保存 cookie 到本地配置。
- 当前本地配置路径：
  - `/Users/lxx/.config/jqcli/config.json`
- 登录状态接口验证成功：
  - `/user/index/isLogin`
  - 返回 `code: "00000"`，`isLogin: 1`

## 策略接口调研

- 策略列表页可访问：
  - `/algorithm/index/list`
- 页面是服务端渲染 HTML，不是简单 JSON API。
- 策略列表 HTML 中观察到：
  - `<tr class="algorithm_list">`
  - 编辑链接：`/algorithm/index/edit?algorithmId=...&backtest=...`
  - 回测列表链接：`/algorithm/backtest/list?algorithmId=...`
- 2026-04-25 继续调研结果：
  - 使用已保存 cookie 请求 `/algorithm/index/list` 成功。
  - 当前账号返回 `10` 条策略列表行。
  - 每行可用正则 `r'<tr class="algorithm_list".*?</tr>'` 提取。
  - 单行中至少有两个疑似 ID：
    - `_algorithmId="..."`，在第一列 checkbox 容器上。
    - 编辑链接中的 `algorithmId=...`。
  - 初步样例，注意不要把这些样例当作固定数据：
    - `href_id`: `41faea7d0299bc92c71364d9cd878259`
    - `attr_id`: `c8546c1d149c650ec420bbee2fe12637`
    - `name`: `混合策略`
    - 清洗后的列：`["", "混合策略", "Code", "2025-10-08 12:48:07", "0", "1", ""]`
    - 第二条：`全天候ETF`，类型 `Code`，更新时间 `2025-07-01 11:18:21`
    - 第三条：`小市值混合`，类型 `Code`，更新时间 `2025-06-18 23:12:16`
  - 中断前正在尝试查看完整单条 `<tr class="algorithm_list">...</tr>`，用于确认哪个 ID 是编辑/删除/回测的真实主键。该命令被用户中断，结果不可用。
- 2026-04-25 已落地 `strategy ls` 真实接口：
  - `jqcli/api/client.py` 增加 `get_text()`，用于读取服务端渲染 HTML。
  - `ApiClient` 使用 `trust_env=False`，避免本机 SOCKS 代理环境变量导致 `socksio` 依赖错误。
  - `jqcli/api/strategy.py` 增加 `parse_strategy_list_html(html)`。
  - `list_strategies()` 已改为请求 `/algorithm/index/list`。
  - parser 当前输出字段：
    - `id`：编辑链接中的 `algorithmId`，暂作为策略主 ID。
    - `internal_id`：行内 `_algorithmId`。
    - `name`、`type`、`updated_at`、`run_count`、`backtest_count`。
    - `created_at` 暂为空字符串，因为列表页暂未确认该字段。
  - 已用真实账号只读验证：
    - `jqcli --env-file .env --format json strategy ls --limit 3`
    - 返回 3 条策略，包括 `混合策略`、`全天候ETF`、`小市值混合`。
  - 已添加 HTML parser 单元测试。
- 2026-04-25 已继续落地其余真实接口：
  - `strategy show`：
    - 请求 `/algorithm/index/edit?algorithmId=...`
    - 解析编辑页表单，输出 `id`、`save_id`、`backtest_id`、`name`，可按需输出源码。
  - `strategy new`：
    - 请求 `/algorithm/index/new`
    - 创建后调用 `/algorithm/index/save` 设置名称和源码。
    - 注意：聚宽列表页的 `algorithmId` 会随请求变化，命令返回会重新从列表按名称解析可用 id。
  - `strategy edit`：
    - 先读取编辑页表单，再 POST `/algorithm/index/save`。
    - 源码按前端逻辑 base64 编码，并提交 `encrType=base64`。
  - `strategy rm`：
    - 请求 `/algorithm/index/del`。
    - 删除必须带 `X-Requested-With: XMLHttpRequest` 和列表页 `Referer`，否则返回 `不合法请求`。
    - 删除时会从当前列表页解析 `_algorithmId`；如果列表页 id 变化，会回退到编辑页名称再匹配当前列表项。
  - `backtest run`：
    - 请求 `/algorithm/index/build`。
    - 返回包含聚宽的 `backtestId_` 和 `backtestId`，CLI 输出为 `id` 和 `list_id`。
    - 默认发起正式回测：提交 `backtest[type]=0`，记录进入 `/algorithm/backtest/list`。
    - 保留编译运行模式：增加 `--compile`，提交 `backtest[type]=1`，记录进入 `/algorithm/backtest/buildList`。
  - `backtest ls`：
    - 默认请求 `/algorithm/backtest/list?algorithmId=...`。
    - `--compile` 请求 `/algorithm/backtest/buildList?algorithmId=...`。
    - 解析 `<tr class="backtest-tr">`，输出详情 id、列表 id、源码 id、状态、区间、资金、频率和基础指标字段。
  - `backtest show`：
    - 请求 `/algorithm/backtest/detail?backtestId=...`
    - 再请求 `/algorithm/backtest/source` 和 `/algorithm/backtest/stats` 获取源码和指标。
  - `backtest rm`：
    - 默认请求 `/algorithm/backtest/del?type=0` 删除正式回测。
    - `--compile` 请求 `/algorithm/backtest/del?type=1` 删除编译运行记录。
    - 已用正式回测列表中的 `list_id` 验证删除成功。
  - `ApiClient` 默认发送 `X-Requested-With: XMLHttpRequest`，写接口按需要补 `Referer`。
  - 新增调研脚本：
    - `scripts/inspect_joinquant_static.py`
    - `scripts/inspect_joinquant_page.py`
  - 真实写入验证：
    - 创建、编辑、删除临时策略成功。
    - 发起短区间回测成功，随后删除回测记录和临时策略成功。
- 2026-04-25 已确认回测两种服务端路径：
  - 聚宽前端正式回测和编译运行都 POST `/algorithm/index/build`，差异在 `backtest[type]`。
  - 正式回测按钮设置 `type=0`，使用 `/algorithm/backtest/list` 和删除 `type=0`。
  - 编译运行路径使用 `type=1`，使用 `/algorithm/backtest/buildList` 和删除 `type=1`。
  - 新增调研脚本：`scripts/inspect_backtest_flows.py`。
  - 真实正式回测验证：
    - 策略：`全天候ETF`
    - 策略 id：`8f345883a69b74cc456919ab1be76267`
    - 时间：`2024-01-02` 到 `2024-01-10`
    - 初始资金：`1000000`
    - 提交返回：`id=55544219`，`list_id=1a5ed7cd1472a7df43824aefc9f2edeb`，`mode=backtest`
    - 正式列表出现新记录：提交时间 `2026-04-25 20:44:41`，状态 `done`
    - 详情指标获取成功：`algorithm_return=0.0053687416399999`，`benchmark_return=0.00328947368421`，`sharpe=3.7356032973192`，`max_drawdown=0.0035792419084949`
    - 测试记录已从正式回测列表删除成功。
- 策略列表页引用的 JS：
  - `https://cdn.joinquant.com/std/algorithm/js/list.min.js`
- 从 JS 中发现的接口：
  - `/algorithm/index/del`
  - `/algorithm/index/save`
  - `/algorithm/index/new`
  - `/algorithm/index/AddFile?...`
  - `/algorithm/index/AlgorithmToFile?...`
  - `/algorithm/index/GetFileList?...`

## 研究平台支持

- 2026-08-14 至 2026-08-15 完成研究工作区调研与实现：
  - 研究平台不复用 `/algorithm/index/*File` 策略文件夹接口，也不是 Community 响应里的 `research.notebook_*` 元数据。
  - 主站 Cookie 先读取 `/default/research/redirect`，通过同源 JupyterHub/OAuth 重定向建立会话，再从页面发现动态 `/user/<user>/` base path。
  - 已实现 Contents 读取、下载、上传、建目录、移动/重命名和删除 API/CLI。
  - 路径拒绝 `.`、`..`、空组件与 NUL；SSO 逐跳校验同源；写请求按实际 Contents 路径选择 `_xsrf`。
  - 远端写命令均需确认；上传默认不覆盖，仅 `--force` 允许覆盖；根目录不能作为写目标。
  - 已用真实账号只读验证 `research ls` 和元数据 `research show`，未读取正文，未发送任何研究资产写请求。
  - 写接口契约依据实例自报的 Jupyter Notebook 5.4.1 同版本官方实现，当前只用 `httpx.MockTransport` 测试固化。
  - 已确认研究实例开放同版本 Jupyter 内核与会话接口：
    - `GET api/kernelspecs` 返回 `200`，规格数量为 `3`。
    - `GET api/kernels` 与 `GET api/sessions` 均返回 `200`，探测时数量均为 `0`。
    - 对随机且确定不存在的标准 UUID 进行普通 `GET api/kernels/<id>/channels` 返回 `404`；Notebook 5.4.1 官方源码确认该 channels 路由及 WebSocket 消息契约。
    - 整个开放性探测保持只读，没有启动、连接或停止任何现有 kernel/session，也没有输出名称、标识或 Notebook 路径。
  - 已增加只读 `research kernelspecs/kernels/sessions`，用于查看可用内核规格及当前内核/会话。
  - 已增加 `research exec` 与 `research run`：
    - `exec` 从 `--file` 或 `--code-stdin` 读取代码；`run` 执行用户明确指定的远端 Notebook，可重复传 `--cell` 选择零基索引单元。
    - `exec` 与 `run` 都创建独占的高熵临时 session 与新内核；`exec` 的合成 session 不保存研究文件，两者绝不复用现有对象，并在成功、错误、超时或中断时从 `finally` 清理。
    - 两个命令均强制显式 `--yes`；支持 `--kernel`、`--execution-timeout` 与 `--stream`。
    - `run` 默认且当前始终不保存输出回远端 Notebook。
  - 已完成最小真实远端执行验收：在独占高熵临时会话中执行 `1 + 1`，返回 `status=ok` 与结果 `2`；执行前后 kernel/session 数量均为 `0`。验收没有读取或保存 Notebook，也没有连接现有内核。
  - wheel 打包已修正为包含 `jqcli.api`、`jqcli.commands`、`jqcli.web` 及 Web 静态资源。

## 重启后建议下一步

1. 补强边界行为：
   - `backtest rm` 当前建议传 `backtest ls` 返回的 `list_id`；后续可增加详情 id 到列表 id 的自动解析。
   - `backtest ls` 的指标字段初始 HTML 里可能为 `--`，后续可调用 `/algorithm/backtest/statsList` 合并实时统计。
2. 如需继续验证错误、超时或中断路径，应仍使用无资产写入的最小临时代码，并在前后比较 kernel/session 数量；不要复用或停止现有对象。
3. 研究平台如需支持超过 25 MiB 文件，先根据真实实例只读证据补齐分块上传契约。
4. 后续可补分页、文件夹策略同名冲突处理、更多错误码映射。

## 注意事项

- 不要打印 `.env`、cookie、密码。
- 真实接口调研时优先只读请求。
- 删除、新建、保存等破坏性接口先确认参数和测试策略后再执行。
- 上一个被中断的命令只是 HTML 解析探测，可能已被用户中断，无需依赖其结果。
