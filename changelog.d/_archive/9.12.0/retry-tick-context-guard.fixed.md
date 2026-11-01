# host 不再盲落 github.com —— 无本批登记表时从 URL 解析
重投路径与「启动期自动重投」下 `_host_by_key` 是空的，而落库/事件/闸门取值原为 `self._host_by_key.get(key) or "github.com"` —— gitlab / gitee / 自建主机的仓库会被**落库成 host="github.com"**，污染 UNIQUE(owner,repo,host)：同一仓库被当成两个，之后「一键更新」指向错误来源。现新增 `engine._host_for()`：取值顺序 = 本批登记表 → 从 url_https 解析（复用了既有的 url_lib.host_of）→ 才回落 github.com；`_start_worker` 与 `_on_result` 统一走它。
独立审查起初建议的「无 _last_batch_ctx 就拒绝重投」被**否决**：那会关掉 tests/test_g67_closure 明确要求的既有契约「快照为空时不得崩、仍要重投」（首次启动就有待重投队列是真实场景）。根因在 host 的取值方式，不在重投本身。
@verify tests/test_c1_wiring.TestHostNotPollutedWithoutBatchContext（3 例：7 种 URL 形态解析 / 登记表优先 / 空上下文重投时落库 host=gitlab.com）· tests/test_g67_closure（既有重投契约回归绿）
