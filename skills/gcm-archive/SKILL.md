# gcm-archive：Git-clone-Max 归档下载技能包（G56-1 生态 agent 化）

为 agent 提供的**仓库归档（zip）下载**入口。脚本直接复用
`gcm/git/archive.py::download_archive`（浅克隆 + `git archive HEAD` → zip），
不写数据库、不改仓库，只产出 zip 文件到指定目录。

## 适用场景
- 只要某个仓库当前 HEAD 的代码快照（zip），不要完整 git 历史。
- 对大量仓库批量打包归档（脚本内串行执行，避免并行 IO 尖峰）。

## 关键约束
1. 只写 `--out` 指定输出目录；**禁止**触碰数据目录/数据库/项目源码。
2. 输入地址先过 `gcm.app.url_lib.parse_any_repo_url` 校验；无效行进入 `invalid` 清单。
3. 归档过程会**触网**（除非用本地裸仓 file://）；调用方须自行承担网络行为。

## 用法

```bash
python skills/gcm-archive/scripts/archive-list.py <urls.txt> --out ./zips [--ref HEAD]
```

## 脚本
- `scripts/archive-list.py`：逐行归档为 `owner__repo@ref.zip`，输出统一 JSON
  `{version, ok, counts:{success,failed,other,total}, results:[{url,status,path,message,code}], invalid:[...]}`，
  退出码 0=全成功 / 1=有失败 / 2=无有效输入。

## 验收清单（本包交付即自检）
1. 对本地裸仓运行：成功产出 zip，`unzip -l` 能看到 `a.txt`。
2. 无效行进入 `invalid` 且不影响有效仓的退出码 0。
3. JSON schema 与 `docs/agent-contract.md` §2 对齐（results 项字段一致）。
