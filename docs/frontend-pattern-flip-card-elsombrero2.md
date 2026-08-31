# ElSombrero2 翻转卡片：实现审计与 MatCreator 吸收方案

状态：设计模式已核验；Remote Jobs 已实现受控翻转与独占展开，皮肤商店仍为参考候选

采集日期：2026-08-28

适用范围：`web/vite-frontend` 的皮肤预览与非关键展示交互；不改变工作流、材料科学逻辑、后端生命周期或 Remote Jobs 语义

## 结论

这个案例值得保留的不是食物卡片造型，而是一个很清楚的“双面卡片转子”模型：外层提供视口，内层作为唯一旋转平面，两张等尺寸面板绝对定位叠放，其中一面预先旋转 180 度；翻转时只改变转子的 `transform`。两面都隐藏背面，因此每个时刻只呈现朝向用户的一面。

它最适合 MC 的落点是 Settings → Appearance 以及未来皮肤商店中的“皮肤预览卡”：概览面展示名称、缩略预览和色板，详情面展示作者、版本、兼容 recipe、许可和能力说明。它不适合消息、时间线、会话虚拟列表、Agent Graph 节点或任何必须持续可见的状态信息。

不能原样照搬演示源码。原案例只支持 hover，没有键盘、触摸和持久状态；未声明透视；隐藏的背面仍可能留在可访问性树和 Tab 顺序中；部分定位与层级依赖演示环境；旋转边框关键帧还有重复 `0%` 的笔误。MC 应保留几何思路，使用自身组件状态、语义 token、图标系统和完整的 reduced-motion 分支重新实现。

## 2026-08-29：Remote Jobs 当前落地

根据当前 UI 迭代要求，这个模式已经以受限形式应用到 Remote Jobs 卡片。此前“不建议用于状态密集区域”的判断仍然成立，因此实际实现加入了以下硬约束来避免翻面隐藏生命周期语义：

- 摘要态保持紧凑；展开后卡片占满 Remote Jobs 横向区域并增高到响应式详情尺寸，其他兄弟卡暂时隐藏，返回摘要后恢复网格。
- 生命周期状态文字与状态色边条在正反两面都存在；背面把 provider state 与 Sandbox/Job 标识压缩为一条保留完整 title 的 reference 行，翻转仅代表“摘要 / 详情与控制”两组信息，不代表任务启动、暂停、终止或完成。
- 整卡摘要/详情表面支持 click、Enter 与 Space 翻面；右上角 refresh 以及详情内真实操作控件被明确排除，不会误触发翻转。卡片自身维护可聚焦语义、`aria-expanded` 与目标面关系。
- 隐藏面同步 `aria-hidden` 与 `inert`。详情面承载既有 refresh / pause / terminate 控件，但按钮仍调用原控制器方法，没有修改 API、轮询或任务生命周期。
- 翻面状态按 `job_id` 保存在前端控制器内，因此 15 秒轮询重建 DOM 时不会把用户自动翻回；切换会话或 reset 时清空。
- reduced-motion 下取消 3D 旋转并即时切换可见面；forced-colors 下退化为系统实色边框和按钮。
- 实现由 MC 重新编写，没有复制案例的 SVG、卡片内容、关键帧或装饰参数。

## 来源、作者与许可

- 案例页：[Card by ElSombrero2](https://uiverse.io/ElSombrero2/tricky-robin-67)
- Uiverse 官方归档：[固定提交中的源码](https://github.com/uiverse-io/galaxy/blob/6284e84cac47be0815cea0f8912403ab9b12cb1c/Cards/ElSombrero2_tricky-robin-67.html)
- 官方归档仓库：[uiverse-io/galaxy](https://github.com/uiverse-io/galaxy)
- 作者账号：`ElSombrero2`
- Uiverse 页面显示姓名：Rakotondrasoa Nirilala
- Uiverse 页面发布日期：2023-02-19
- 页面当前版权提示：Copyright - 2026 ElSombrero2 (Rakotondrasoa Nirilala)
- 页面当前许可提示：MIT License；页面同时声明站内 UI elements 以 MIT 发布
- 归档文件提交：`6284e84cac47be0815cea0f8912403ab9b12cb1c`，提交日期 2023-10-01
- 该固定版本内容 SHA-256：`9a7f1461e87d637577345d5f66a581898e91198e77c6da3132503069b08b0e54`

MIT 允许使用、修改和再分发，但要求在软件的全部或实质部分保留版权与许可声明。如果 MC 后续直接改编了可识别的源码结构或参数，应把作者、原链接、MIT 文本和改动说明加入第三方声明。本记录不是法律意见。

源码中的内联图标带有 `SVGRepo_*` 标记，而案例页没有为该图标单独给出来源链。MC 不应复制这段 SVG path，应改用 MC 自有、许可已经登记的图标系统。这样翻转机制的采用不会顺带引入来源不清的资产。

## 原始 HTML/CSS 骨架

### DOM 分层

原案例的结构可以抽象为：

```text
card viewport
└─ content / rotor
   ├─ back face
   │  ├─ rotating gradient border (pseudo-element)
   │  └─ inset content
   └─ front face
      ├─ blurred color blobs
      └─ badge + description surface
```

`.card` 只规定卡片视口尺寸并允许外部阴影溢出；`.content` 占满视口，是唯一被翻转的转子；`.front` 与 `.back` 都绝对定位、等宽等高、共享圆角和裁切。源码首屏实际显示 `.back`，hover 后才显示预旋转的 `.front`，所以它的 front/back 命名不应直接移植到 MC。MC 应使用 `face--summary` 与 `face--details` 这类按内容语义命名的类。

### 3D 翻转合同

案例真正必要的属性只有以下几个概念：

```css
transform-style: preserve-3d;
backface-visibility: hidden;
transform: rotateY(180deg);
```

- 转子使用 `transform-style: preserve-3d`，让两张面板保持同一个 3D 子空间。
- 两面使用 `backface-visibility: hidden`，其中一面初始为 0 度，另一面预先为 180 度。
- hover 时转子整体旋转 180 度，而不是分别动画两张面板。
- 只动画 `transform`，没有 JavaScript，也没有布局尺寸动画。

原实现没有声明 `perspective`。因此背面隐藏和换面仍有效，但旋转缺少明显的近大远小与纵深。MC 应在不参与旋转的外层设置受控透视，例如把 `perspective` 作为 recipe 内部几何参数；不要把它放在转子自身，也不要允许皮肤清单注入任意 transform 字符串。

原实现也没有为转子明确设置 `position: relative`。两张绝对定位面板会寻找其他包含块，移出演示容器后存在定位漂移风险。MC 版本必须让卡片视口或转子成为明确的定位上下文，并使用 `inset: 0` 对齐两面。

### 触发器、节奏与 easing

- 触发条件是 `.card:hover`，没有 click、focus、checkbox 或 JavaScript 状态。
- 翻转持续 300ms；源码未指定 timing function，因此使用浏览器默认的 `ease`。
- 详情面的三个模糊色块采用约 2.6s 的无限线性上下浮动，并使用负延迟错开相位。
- 首屏面的渐变条伪元素采用约 5s 的无限线性旋转，内部实色面板覆盖中心，从而只露出动态边框。

源码的旋转边框关键帧写了两次 `0%`，第二段显然应当是终点。MC 若吸收这一思路，必须用自己的 0→100% 动画重新实现，不能复制这个错误。翻转 easing 建议由 MC recipe 固定为短促、可预测的 UI 曲线；皮肤只改变材质，不应让不同皮肤任意改变交互时长。

### 阴影、滤镜与层级

- 转子本身承载一层深色外阴影，使两面翻转时共享同一投影。
- 详情文字位于半透明深色表面上，并叠加局部阴影与 `backdrop-filter`。
- 色块的 blur 是静态滤镜，运动部分仍然只改变 transform。
- 动态边框由 `::before` 渐变层和一个近乎满尺寸的实色内容层叠出。

原演示没有显式 `isolation` 或 z-index 合同，层次主要依靠伪元素与后续子元素的绘制顺序。MC 应显式隔离卡片堆叠上下文，给装饰层、面板层和交互层定义固定层级，并让所有装饰层 `pointer-events: none`。大阴影、blur 和 backdrop-filter 只能用于少量固定尺寸预览卡，不能进入虚拟列表或大面积滚动热区。

## MC 中的建议落点

### P0：Appearance / 未来皮肤商店预览卡

建议的信息分工：

- 概览面：皮肤名称、预览色板、当前 variant、选中状态和静态缩略图。
- 详情面：作者/发布者、版本、许可、MC 兼容范围、`styleRecipe` ID/API version、可访问性说明。
- 卡片外部或固定位置：真实的选择 radio 与“查看详情/返回概览”按钮。

翻转只表示“查看另一组信息”，不能代表安装、下载、启用、运行或完成。选择皮肤仍由原生 radio/明确按钮承担；不能让一次 hover 偷偷改变当前皮肤。

### P1：只读技能/能力目录卡

如果以后技能目录需要在不离开网格的情况下展示简短元数据，可用同一组件在概览与权限/来源说明之间切换。安装、启用、删除或权限确认仍应位于明确的常规控件中，不能藏在仅 hover 可达的背面。

### 明确不采用

- 流式消息、时间线条目和会话列表：翻面会隐藏上下文，并干扰虚拟化测量与阅读锚点。
- Agent Graph 节点：它是 Canvas 渲染，且节点类型与生命周期状态必须同时可见。
- Composer、表单和关键设置：用户不能在输入过程中因指针移动丢失当前表面。
- 错误、警告、运行状态和科学语义：状态不得靠卡片朝向、装饰色或动画来表达。

## 与皮肤内核的集成边界

翻转的 DOM、状态机、选择器与关键帧必须由 MatCreator 拥有。皮肤包仍是经过校验的数据包，不能携带此案例的 HTML/CSS，也不能提供远程样式、SVG 或脚本。

可由现有语义 token 提供的部分包括：

| 视觉职责 | 推荐 token 来源 |
| --- | --- |
| 两面基础表面 | `--bg-surface`、`--bg-elevated` |
| 主次文字 | `--text-primary`、`--text-secondary`、muted 语义 |
| 边框与分隔 | `--border-default`、strong/subtle 边框语义 |
| 装饰高光 | `--accent-primary` 或皮肤结构 accent；不得复用状态色 |
| 选择与键盘焦点 | selection 语义、`--focus-ring` |
| 圆角与投影 | skin structure 的 radius/elevation/tactile shadow |

透视深度、180 度换面、层级、翻转时长、easing 和 reduced-motion 行为属于交互合同，应由 MC recipe/组件固定，而不是作为自由字符串开放给商店皮肤。如果将来确实要开放时长等能力，应先给 `SkinContract` 增加有类型、有范围的 time/easing 字段，不能退回任意 CSS 值。

当前 skin schema 每个皮肤只有一个 `styleRecipe`，并不是可组合 recipe 数组。因此本模式落地时有两种安全选择：

1. 把翻转作为所有皮肤共享的 MC 组件行为，只让各皮肤提供现有语义 token；或
2. 把其材质增强作为现有预打包 recipe（例如 `rack-lab@1`）中的受限组件规则，同时保留 standard recipe 的静态/简化版本。

在 schema 支持经过审查的能力组合之前，不应擅自让清单声明第二个 `flip-card` recipe。无论选择哪条路径，换肤都只更新 token/data attributes，不重建卡片状态或其他行为组件。

## 交互与可访问性补强

### 显式状态替代 hover-only

MC 应由真实按钮切换一个组件状态，例如 `data-flipped="true|false"`。按钮使用“查看皮肤详情/返回皮肤概览”一类随状态变化的可见文本，并通过 `aria-expanded` 和 `aria-controls` 与详情区域建立关系。Enter 与 Space 必须和点击完全等价；触屏单击也使用同一状态机。

hover 只能作为细指针设备上的非关键预览增强，并限制在 `hover: hover` 且 `pointer: fine` 的环境。更稳妥的默认是 hover 只产生抬升/高光，真正翻面仍由显式按钮触发，避免用户移动指针时内容反复消失。

### 两面的可访问性树

`backface-visibility` 只改变绘制，不会自动隐藏屏幕阅读器内容或背面控件。必须同步维护：

- 当前不可见面设置 `inert`，并按兼容策略同步 `aria-hidden="true"`；
- 不可见面的链接、按钮和表单项不能留在 Tab 顺序中；
- 若焦点位于即将隐藏的一面，先把焦点交给翻转按钮，再改变状态；
- 卡片不能做成包含其他按钮的“大按钮”，避免嵌套交互元素；
- 皮肤选择 radio 与详情翻转按钮应是两个独立语义动作。

焦点环必须画在不会被面板 `overflow: hidden` 裁切的交互层上。翻转之后，按钮的可见标签和 `aria-expanded` 已足以表达状态，不需要为纯装饰动画增加 live region。

### 触摸与窄屏

- 翻转按钮至少 44×44 CSS px，不依赖角落小图标。
- 粗指针设备禁用自动 hover 翻转；一次 tap 只执行一次明确状态变化。
- 正反面必须共享可预测的最小高度，详情文案过长时应正常排版或进入面内滚动，不能撑破 App Shell。
- 在 `<= 640px` 验收焦点环、长作者名、中文/英文混排和 200% 文本缩放。
- 设备方向改变或响应式重排不能重置用户已经打开的详情状态。

### Reduced motion

在 `prefers-reduced-motion: reduce` 下：

- 禁用 Y 轴翻转，改为近乎即时的内容切换或很短的 opacity 交叉淡化；
- 停止旋转边框、色块漂浮和所有无限循环动画；
- 保留静态实色边框、固定高光、面板层级和明确按钮文字；
- 不因关闭动画而同时隐藏详情、选中、许可或兼容性信息。

`forced-colors` 或不支持 backdrop-filter 时应退化为实色表面和系统可见边框。装饰性的橙红色不能承担错误、警告或选中含义。

## 实施前验收清单

- [ ] 使用 MC 自有 DOM、类名、图标和参数，没有复制来源不清的 SVG。
- [ ] 外层有明确 perspective，转子有定位上下文，两面尺寸与层级稳定。
- [ ] 使用显式 click/tap/keyboard 状态；hover 不是唯一入口。
- [ ] 隐藏面同步 `inert`/`aria-hidden`，Tab 不会进入背面。
- [ ] 翻转按钮、皮肤选择与卡片内其他动作没有嵌套语义。
- [ ] reduced-motion、粗指针、无 backdrop-filter 与 forced-colors 均有静态回退。
- [ ] 装饰层不截获指针，focus ring 不被裁切，文字和边界对比可读。
- [ ] Default Dark/Light 与 Rack Lab Cream/Graphite 均完成桌面、`<= 900px`、`<= 640px` 验收。
- [ ] 不进入消息、时间线、虚拟会话或状态密集区域；不导致重测量、重连或状态重建。
- [ ] 若直接改编可识别源码，第三方声明保留作者、链接、MIT 文本和修改说明。
