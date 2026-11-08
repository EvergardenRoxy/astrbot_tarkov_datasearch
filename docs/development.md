# 完整字体与图片开发

所有图片通过 `tarkov_core/fonts.py` 使用同一份完整 Noto Sans SC 字体，不需要收集中文字符、制作裁剪字库或修改字体文件。Oswald 和 Share Tech Mono 仍随插件提供，字体许可见 [THIRD_PARTY.md](../THIRD_PARTY.md)。

插件首次生成图片前，会通过现有代理和镜像设置下载固定版本的完整字体（约 17 MB），校验大小与 SHA-256，存入插件数据目录的 `fonts/`。并发请求共用一次下载，字体下载失败时给出文字提示，30 分钟后可再次尝试。已经缓存并校验的字体会跨重启复用。

本地测试或生成示例图之前，在仓库根目录执行：

```sh
python -m pip install -r requirements.txt
python tools/download_font.py
python -m unittest discover -s tests -v
```

准备脚本只下载并校验原始字体文件，缓存到 `.cache/fonts/`，不生成字库。该目录不会提交到 Git，也不包含在插件安装包中。测试不会自行下载字体或查询真实玩家；CI 在测试前单独准备字体。

离线使用时，可指向已取得的同版本文件：

```sh
TARKOV_TEST_FONT='/absolute/path/NotoSansSC[wght].ttf' python -m unittest discover -s tests -v
```

示例图生成脚本使用相同缓存，也支持 `TARKOV_TEST_FONT`：

```sh
python tools/build_preview.py
python tools/build_arena_legacy_preview.py
```

其中玩家与竞技场示例使用虚构数据；`build_public_preview.py` 和不传已保存响应的 `build_matching_preview.py` 会读取公开服务数据。新增中文文案或显示新的成就名称时，不需要额外更新字符清单。
