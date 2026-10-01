## 1. Regression Contracts

- [x] 1.1 Add failing tests that keep the primary action in disabled `停止` state while report and construction-plan review are pending.
- [x] 1.2 Add failing tests proving reviewed, checked rendering runs in a worker while GUI events remain responsive.
- [x] 1.3 Add terminal-path tests for render completion, cancellation, failure, empty selection, unchecked rendering, and blocked disposition.
- [x] 1.4 Require the complete `生成修复渲染图` label in compact and wide layouts.

## 2. Workflow Implementation

- [x] 2.1 Add a `RepairRenderWorker` that reuses `FhlRepairRenderer` callbacks and supports stopping remaining items.
- [x] 2.2 Preserve the primary running appearance through both review gates and start asynchronous rendering after eligible confirmation.
- [x] 2.3 Centralize render completion, cancellation, failure, empty-selection and blocked terminal state handling.
- [x] 2.4 Integrate render-worker stopping and shutdown with the primary action and window close lifecycle.
- [x] 2.5 Display `生成修复渲染图` without responsive abbreviation.

## 3. Verification

- [x] 3.1 Run focused workflow, GUI contract and adaptive-layout tests with the Qt project interpreter.
- [x] 3.2 Run the complete test suite and Python compilation checks.
- [x] 3.3 Run strict OpenSpec validation and inspect an offscreen workflow-state screenshot.
