# changelog.d — 待发布的改动碎片

每个**用户可见的改动**在这里放一个小文件，发布时由脚本聚合成一条完整的版本条目。
这样做的原因见 `scripts/changelog.py` 顶部与 `gcm/_changelog_data.py` 的说明：
把 changelog 从 `gcm/__init__.py` 里拆出来，避免每次提交都在同一个巨型 tuple 上冲突。

## 新建一条

```bash
python scripts/changelog.py new \
    --category fixed \
    --slug mirror-cred-leak \
    --title "镜像克隆后不再把凭据发给第三方" \
    --detail "认证头改 URL 作用域；origin 在克隆成功后立即回源站。" \
    --verify "test_g58_mirror_failover 43 例绿"
```

生成 `mirror-cred-leak.fixed.md`：

```markdown
# 镜像克隆后不再把凭据发给第三方
认证头改 URL 作用域；origin 在克隆成功后立即回源站。
@verify test_g58_mirror_failover 43 例绿
```

## 命名规则

`<slug>.<category>.md`

| category | 什么时候用 |
|----------|-----------|
| `added` | 新增能力 / 新增接口 / 新增 UI 入口 |
| `changed` | 行为或契约发生变化（含 breaking） |
| `fixed` | 修掉一个真实缺陷 |
| `internal` | 纯内部重构、性能优化，用户不可见 |

`slug` 用小写字母、数字、`.`、`-`，能看出改的是什么。

## 发布时

```bash
python scripts/changelog.py check                      # 先确认格式与版本一致
python scripts/changelog.py build --version 9.11.0      # 聚合成一条，碎片归档
```

`build` 会把碎片移到 `_archive/<版本>/` 并在 `gcm/_changelog_data.py` 顶部插入一条。
之后按 `docs/RELEASE_CHECKLIST.md` 同步 `__version__` 与其余版本载体。

## 不要做的事

- **不要**手改 `gcm/_changelog_data.py` 里已发布的历史条目（那是历史记录；
  唯一例外是改错别字，并在 commit message 里说明）。
- **不要**把碎片写成一个版本的全部内容 —— 一条碎片讲一个改动。
