# Rack Lab Remote Jobs 液态玻璃卡片

- 状态：`ACTIVE`（Rack Lab 专属试验）
- 效果 ID：`remote.card.rack.liquid-glass`
- 更新日期：2026-08-29

## 目标与边界

本试验把 Rack Lab 的 Remote Jobs 正反翻转卡面改造成液态玻璃材质，同时完整保留已有任务语义和交互合同：

- 整卡 click、Enter、Space 翻转；
- 翻开后占满 Remote Jobs 横向区域并隐藏其他卡；
- 右上刷新、暂停、终止不会误触翻转；
- `aria-hidden`、`inert`、`aria-expanded`、状态文字和进度语义不变；
- 不改 Remote Job 生命周期、轮询、提交、恢复或后端接口；
- 精确限定在 `body[data-skin="rack-lab"]`，其他皮肤不会继承本试验。

## 官方来源与许可

设计与材质分层参考 [rdev/liquid-glass-react](https://github.com/rdev/liquid-glass-react)，审计基线为提交 [`ac48eab`](https://github.com/rdev/liquid-glass-react/commit/ac48eab18d1f7f444ae30002d240cae29c863a21)，npm 包版本 `1.1.1`。

上游采用 MIT 许可，版权声明为 `Copyright 2025 MAX ROVENSKY`；许可文本见上游 [LICENSE](https://github.com/rdev/liquid-glass-react/blob/ac48eab18d1f7f444ae30002d240cae29c863a21/LICENSE)。MC 没有复制上游的 Base64 位移贴图、React 组件或 shader 生成器，而是针对现有原生 DOM 独立实现共享 SVG 噪声位移、CSS 材质层和列表级指针控制。源码注释仍保留概念来源与许可信息，方便继续审计。

## 上游设计结构

上游有三个不可混合的层：

1. `backdrop-filter` 采样真实背景，SVG `feDisplacementMap` 只对玻璃背景产生位移与轻微 RGB 色散；
2. 两个 masked border 层分别以 `screen` 和 `overlay` 生成方向性边缘高光；
3. 正文位于独立高 z-index 层，上游明确标注 “user content stays sharp”。

因此，“文字也使用液态玻璃形式”在 MC 中解释为：阶段、状态和配置字段由半透明玻璃铭牌承载，并带湿润的顶缘高光和轻微字面阴影；ID、状态、配置值本身始终保持锐利、可选择和高对比，不把字形送入位移滤镜。

## MC 原生实现

### DOM 与滤镜

`RemoteJobsController.js` 只在 Rack Lab 的每个正反面插入两个 `aria-hidden="true"` 装饰节点：

- `.remote-job-glass-warp`：背景采样和边缘位移；
- `.remote-job-glass-edge`：双层 masked edge 与状态色折射边。

控制器在页面中只创建一个共享 SVG `<defs>`。固定的低成本 `feTurbulence` 位移场经过三组小幅不同的 RGB displacement、screen 合并和极轻 blur，避免每张卡生成贴图或运行持续 shader。只有 `aria-hidden="false"` 的卡面启用完整滤镜，隐藏面停止 backdrop/filter 绘制。

### 指针与性能

Remote Jobs 列表只有一组委托 `pointermove` / `pointerout` 监听：

- 使用 `requestAnimationFrame` 合并同一帧的指针更新；
- 只更新当前卡片的 `--rack-glass-x/y`、高光角度、微小倾角与亚像素位移；
- 不创建每卡动画循环，不触发 Remote Jobs render，不改列表测量；
- 移出后通过较慢的弹性过渡复位；粗指针和 reduced-motion 不启用跟随。

卡面只有一个主要 backdrop 层；阶段、状态和字段铭牌使用普通半透明背景，避免真实任务数量增加时为每行都创建昂贵的合成层。

## 渐进增强与可访问性

- Chromium：磨砂背景、共享 SVG 边缘位移、轻微色散、指针方向高光；
- Safari / Firefox 或不完整 filter 支持：保留磨砂、边缘和清晰文字，位移不可见也不影响使用；
- 无 `backdrop-filter`：回退为不透明度更高的 Rack 实色表面；
- `prefers-reduced-motion: reduce`：停止倾斜、位移跟随和滤镜运动，沿用现有立即正反面切换；
- `prefers-reduced-transparency: reduce`：使用实色 Rack surface，隐藏装饰玻璃层；
- `forced-colors: active`：使用 Canvas / CanvasText / Highlight，移除 blur、渐变、shadow 和玻璃层；
- 窄屏与 `@container remote-jobs (max-width: 270px)`：关闭 RGB 位移和弹性，保留静态玻璃与现有详情单列布局。

## 实现与测试入口

- 材质与回退：`web/vite-frontend/src/styles/rackLabLiquidGlass.css`
- DOM、共享 SVG、指针委托：`web/vite-frontend/src/features/remoteJobs/RemoteJobsController.js`
- 样式入口：`web/vite-frontend/src/styles/index.css`
- 结构/交互回归：`web/vite-frontend/test/remoteJobs.test.js`
- 样式与作用域回归：`web/vite-frontend/test/rackLabLiquidGlass.test.js`
- 既有翻转合同：`web/vite-frontend/test/remoteJobFlipCss.test.js`

## 后续可调参数

以后审阅时优先只调整下列材质参数，不重写 Remote Jobs 结构：

- `--rack-glass-blur`：背景磨砂强度；
- `--rack-glass-face-alpha`：卡面遮罩透明度；
- SVG displacement 的三组 scale：折射与色散强度；
- `.remote-job-glass-warp` 的边缘 mask 宽度；
- 指针 tilt 与 shift 上限；
- 状态色在 edge gradient 中的占比。

若卡片数量或集成显卡上出现合成压力，降级顺序应为：先关闭 RGB displacement，再降低 blur，最后保留静态半透明边缘；不得牺牲文字可读性、状态文字或操作语义。
