# 搜索双路：≥3 字走 FTS5，<3 字退回 LIKE（中文两字词终于搜得到）
纯 FTS5 trigram 对中文是残废的：实测 '克隆'/'机器'/'学习' 全部零命中，只有 ≥3 字符才命中原设计照抄会在上线当天被报「搜索坏了」。现在 db.search_repos 收口成双路：≥3 字符走 FTS5（bm25 相关性排序），<3 字符走 LIKE 下推（3000 行实测 3.9ms），两条路返回同形状的行。
索引是**派生**的：repo_fts 是 content='repos' 的外部内容表，应用启动与每批结束各对齐一次（无触发器，避免 3000 仓导入时每次 upsert 多一轮索引写）；索引不可用或查询异常时自动降级 LIKE。当前 SQLite 不带 FTS5/trigram 时整个应用仍可用。
查询语法（纯函数 parse_query）：tag: / host: / favorite: / updated:<30d 与 - 负向；tag 按逗号边界精确匹配（tag:py 不会命中 python）；**lang:/size:/archived: 明确报「不支持」而不是静默忽略**。
@verify tests/test_c4_search（24 例：解析器 8 / 双路 6 / 结构化过滤 7 / 索引派生与降级 3）
