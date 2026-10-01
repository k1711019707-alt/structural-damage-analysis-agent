# Proposal: GPU 加速知识库导入

## Why

知识库导入中的扫描 PDF 会逐页渲染并调用 RapidOCR。当前环境虽然有 CUDA GPU，但锁定的是 CPU-only ONNX Runtime，导致 OCR 全部运行在 CPU，导入速度较慢；同时用户无法从界面判断实际 OCR 后端。

## What Changes

- 检测 ONNX Runtime 是否提供 `CUDAExecutionProvider`，可用时让 RapidOCR 的检测、分类和识别会话使用 GPU。
- 保存实际 OCR 后端诊断（GPU/CPU、provider、设备号）并在导入状态中显示。
- GPU provider 不可用、初始化失败或运行失败时，自动回退 CPU，不丢失已完成导入结果。
- 将环境依赖从 CPU-only `onnxruntime` 切换为带 CUDA provider 的 `onnxruntime-gpu`，保留运行时检测和 CPU 兼容回退。
- 保持文本型 PDF 不调用 OCR；GPU 只加速实际 OCR 阶段，不改变分块、索引和 provenance 契约。

## Non-Goals

- 不用 GPU 加速 PyMuPDF 原生文本提取、SQLite 写入或普通 DOCX 解包。
- 不强制要求没有 CUDA 的机器安装或使用 GPU。
- 不通过降低 OCR 分辨率、跳过页面或删除质量校验来换取速度。

## Impact

- 影响 `runtime/knowledge_base.py`、GUI 导入状态、依赖锁定和 OCR 测试。
- GPU 不可用时行为保持兼容，只增加明确的 CPU fallback 诊断。
