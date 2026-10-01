# 知识管线模块测试启动脚本

这些脚本使用项目当前 `knowledge_pipeline` 模块的入口，不复制模块内部逻辑。每个脚本顶部都直接写出了完整的输入/输出路径（例如 `INPUT_PATH`、`OUTPUT_PATH`、`OUTPUT_DB_PATH`），打开对应脚本即可查看和修改。所有结果默认写入本目录的 `results/`，不会写入生产知识库。

建议在项目根目录使用项目解释器运行：

```powershell
& 'D:\anaconda\envs\YOLO11-HAI\python.exe' 'E:\桌面\海之子\YOLO11-seg\knowledge_pipeline\test\test_pdf_convert.py'
```

确定性主链路的运行顺序为：

```text
test_pdf_convert.py
→ test_chunk.py
→ test_index.py
→ test_retrieve.py
→ test_rerank.py
→ test_generate.py
```

也可以直接启动 `test_rag_pipeline.py` 一次性验证上述链路。`test_embed.py` 需要本地 `sentence-transformers` 和模型权重；成功后才能运行 `test_semantic_retrieve.py`。这两个脚本缺少依赖或 sidecar 时会明确返回失败，不会伪造语义检索成功。

PDF 脚本使用当前 v3 解析入口并传入 `--offline-docling`，因此不会因测试启动而主动下载 Docling 模型；视觉 Responses API 只有显式传入模块的 `--vision` 参数才会启用，本测试脚本默认不发起远程请求。
