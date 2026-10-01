# GUI Full Inspection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the static damage-recognition GUI aggregate, render, report, and list every inspection finding.

**Architecture:** Add a public runtime inspection method that reuses the existing same-class overlap aggregator, then route both CLI static inference and the GUI recognition worker through it. Preserve `infer()` and `infer_instances()` for selected-target and tracking compatibility.

**Tech Stack:** Python 3.11, NumPy, OpenCV, PySide6, pytest, OpenSpec.

---

### Task 1: Lock Runtime Inspection Behavior

**Files:**
- Modify: `tests/test_yolo_segmentation_runtime.py`
- Modify: `runtime/yolo_segmentation_runtime.py`

**Steps:**

1. Add a failing test that calls `infer_inspection()` with overlapping same-class masks.
2. Assert one aggregated finding, retained raw instances, member indices, and selected-target compatibility fields.
3. Run the focused test and confirm failure because `infer_inspection()` does not exist.
4. Implement `infer_inspection()` using `infer()` plus `aggregate_inspection_instances()`.
5. Change `process_path()` to call `infer_inspection()` and remove duplicate aggregation.
6. Run focused runtime tests and confirm they pass.

### Task 2: Route GUI Worker Through Inspection

**Files:**
- Modify: `tests/test_damage_workflow_gui_contract.py`
- Modify: `runtime/damage_workflow_gui.py`

**Steps:**

1. Add a failing source-contract test requiring `runtime.infer_inspection(image)` in `RecognitionWorker`.
2. Run the focused test and confirm it fails on the current `runtime.infer(image)` call.
3. Replace the worker call with `infer_inspection(image)`.
4. Confirm overlay, measurement visualization, and serialized findings consume the inspection result.
5. Run the focused contract test and confirm it passes.

### Task 3: Display Every Finding Row

**Files:**
- Modify: `tests/test_damage_workflow_gui_contract.py`
- Modify: `runtime/damage_workflow_gui.py`

**Steps:**

1. Add a failing Qt test passing two findings to `on_image_done()`.
2. Assert the result table gains two rows with both class names.
3. Run the test and confirm it fails because the GUI inserts only `findings[0]`.
4. Update `on_image_done()` to insert one row per finding and retain one fallback row for failure/no-detection.
5. Run the focused GUI tests and confirm they pass.

### Task 4: Validate End-to-End Compatibility

**Files:**
- Update: `openspec/changes/full-inspection-mask-merge/tasks.md`

**Steps:**

1. Run runtime and GUI-focused suites.
2. Run the entire test suite with bytecode writes disabled.
3. Validate the OpenSpec change.
4. Run a GUI-worker smoke inference on the four representative images or an equivalent service-level smoke if opening the interactive GUI is unsuitable.
5. Record completed tasks and hand off the exact output location and invocation behavior.
