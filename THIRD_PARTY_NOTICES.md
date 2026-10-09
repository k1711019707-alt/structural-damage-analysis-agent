# 第三方声明

Copyright (c) 2026 项目贡献者。

本项目原创代码使用 GNU Affero General Public License v3.0（AGPL-3.0），完整文本见 [LICENSE](LICENSE)。此授权不替代第三方组件、模型和资料的原有许可，不授予项目贡献者并不拥有的权利。

## 依赖与模型

- **Ultralytics**：本项目使用 Ultralytics 进行 YOLO 分割。当前安装包包含 AGPL-3.0 许可证；相关组件及模型还应遵守上游许可。参见 [Ultralytics](https://github.com/ultralytics/ultralytics) 和 [许可说明](https://www.ultralytics.com/license)。
- **PyMuPDF**：用于 PDF 解析，采用 AGPL-3.0 或商业许可。参见 [PyMuPDF](https://github.com/pymupdf/PyMuPDF)。
- **PySide6 / Qt**：用于桌面界面，具有 LGPL、GPL 或商业许可选项，具体取决于所使用的组件。分发程序时应保留相应许可并满足适用条件。参见 [Qt for Python 许可](https://doc.qt.io/qtforpython-6/licenses.html)。
- **BGE 嵌入模型**：`BAAI/bge-small-zh-v1.5` 模型卡声明 MIT 许可，下载或随发行包分发时应保留模型的上游声明。参见 [模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)。模型权重未提交到本仓库。
- **其他依赖**：`requirements-lock.txt` 和 `environment.yml` 中各软件包保留各自的许可证。分发安装包或便携版时应一并核对实际包含的依赖与许可证。

## 模型权重与参考资料

`models/best.pt` 是项目损伤分割权重，基于 Ultralytics YOLO 技术栈训练。使用或再分发权重时，应同时考虑上游模型及训练数据的许可；公开仓库不构成对训练数据权利的额外保证。

`knowledge_pipeline/test/测试文档/` 中的 PDF 是第三方规范、论文或文章，用于管线测试；作者、出版方等权利人保留其版权。项目 AGPL-3.0 许可证不适用于这些资料，也不自动授予其转载、商业使用或再分发权限。使用者应独立核实原始来源及适用授权。

## 外部服务

报告模型、FHL、硅基流动等外部服务按供应商条款使用。源码开放不提供 API 凭据、服务额度或供应商模型的额外许可。
