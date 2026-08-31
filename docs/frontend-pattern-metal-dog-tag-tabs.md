# MatCreator 前端模式：Rack Lab 金属狗牌 Tab

状态：设计研究已吸收；`rack-lab@1` 的 Rack 与 Mental Center tabs 已落地\
采集日期：2026-08-28\
适用范围：`web/vite-frontend` 中间面板的 `.center-tab`，首个落点为 `#tab-chat`；不改变 tab 状态机、会话命名、面板生命周期或 Remote Jobs 语义

## 结论

最适合 MatCreator 的不是下载一张狗牌图片，也不是复制一个复杂 3D 按钮，而是保留现有原生 `button[role="tab"]`，用 Rack Lab 随包 CSS 把其视觉层独立画成一枚紧凑的横向金属身份牌：圆钝短边、左侧孔眼、静态拉丝、上下缘高光/暗边和轻微压印文字。选中状态仍由 `aria-selected="true"` / `.active` 驱动，焦点仍画在未裁切的真实 button 上。

当前实现保留现有 DOM 和伪元素材质层：牌体、孔眼与标题都不增加可访问性树中的装饰节点。`main.js` 只在现有 tab 上更新受限的倾斜与条纹位置 CSS 变量，不改变 tab 的选择、编辑、焦点或面板生命周期。

狗牌没有常驻扫光。拉丝、边缘反射和孔眼在静止时不运动；细指针进入后，牌面根据局部指针位置轻微倾斜，金属条纹随观察方向滑动，移出后复位；pressed 仍只做短下压。选中状态由文字、边界和安装位置共同表达，不把整块牌涂成信号黄。

## 来源、作者与许可

### 实物形态依据

以下页面用于核对“狗牌是什么样”，不复制其照片或图像资产：

| 来源 | 可核验事实 | 复用边界 |
| --- | --- | --- |
| [Smithsonian：James E. Brown II 的越战狗牌](https://www.si.edu/object/nmaahc_2013.11.2) | 银色金属；矩形、圆钝两端；文字深压入金属；有吊孔、细小凹痕和磨损 | 页面元数据为 CC0，但图像页面另有使用条件；MC 不下载、不打包、不描摹照片，只采用通用物理特征 |
| [Smithsonian：Robert S. Jennings 的二战狗牌](https://nmaahc.si.edu/object/nmaahc_2022.91.5ab) | 长圆形；一端有链孔；另一端有半圆缺口；文字冲压；实物约 2.8 × 5 cm | 只用于比例与历史形态研究，不复制媒体资产 |
| [Smithsonian：Identification Tag and Cover](https://americanhistory.si.edu/collections/object/nmah_1357636) | 实物材料为铝；约 1 1/8 × 2 × 1/16 英寸 | 支持“薄铝片而非厚重钢块”的材质判断 |
| [美国陆军军需博物馆：Identification Tags 简史](https://qmmuseum.army.mil/research/history-heritage/mortuary-affairs/Short-History-of-Identification-Tags.html) | 二战后采用熟悉的长圆形；1941–1970 年代缺口来自压印机定位，现代牌边缘平滑 | MC 默认不做历史缺口；如做切角，只作为 Rack Lab 自有工业语言，不能宣称为现代军牌标准 |

由此得到的形态优先级是：**长圆矩形 > 一端孔眼 > 压印文字 > 细拉丝/磨痕**。历史缺口、珠链和真实个人信息都不是 tab 必需特征。

### 可追溯的 CSS 金属光学参考

Uiverse 的两个示例不是狗牌，而是金属开关。它们值得参考的只有“交替明暗的渐变可让小面积 UI 读成金属”这一通用光学方法：

| 项目 | 核验结果 |
| --- | --- |
| 展示页 | [Switch by vinodjangid07 / lucky-mole-65](https://uiverse.io/vinodjangid07/lucky-mole-65)；页面作者为 Vinod Jangid，发布日期 2023-03-30，标签含 metal / metallic / skeuomorphism |
| 固定源码 | [Galaxy 固定提交中的 `lucky-mole-65`](https://github.com/uiverse-io/galaxy/blob/8bca8c80a81833091e56f52c747a79c8ad2a3fd0/Toggle-switches/vinodjangid07_lucky-mole-65.html)；该提交作者 `vinodjangid07`，时间 2023-10-01T17:31:13Z |
| 补充示例 | [Switch by vinodjangid07 / lazy-cheetah-23](https://uiverse.io/vinodjangid07/lazy-cheetah-23)；[固定源码](https://github.com/uiverse-io/galaxy/blob/2e1459386ab646d0de8a38497e6b19a94d544148/Toggle-switches/vinodjangid07_lazy-cheetah-23.html) |
| 许可 | 两个展示页均显示作者的 MIT License；Galaxy 仓库也以 [固定提交中的 MIT License](https://github.com/uiverse-io/galaxy/blob/adbd2adde0a299a3956ea288fb444ec01891ca41/LICENSE) 发布，版权行为 `Copyright (c) 2023 Uiverse.io` |
| 实现观察 | 圆形活动件使用 `conic-gradient()` 交替排列灰/白扇区，并用单层投影建立金属反射和离面高度 |

原示例不能原样迁移：它们把真实 checkbox 设成 `display: none`，缺少键盘焦点与 forced-colors 回退，且构型是 toggle，不是 tab 或狗牌。MC 只提炼渐变分层方法，DOM、参数、选择器、状态和可访问性全部独立实现。若未来直接复制或实质改编可识别代码，必须在第三方声明中保留作者、固定源码链接与 MIT 文本；本方案没有这种需求。

未采用许可不明的素材网站图片、随机 CodePen/Gist 或搜索结果缩略图。GitHub 官方许可说明指出，没有许可证的公开仓库默认仍受版权保护，仅“可查看/可 fork”不等于可复制发布：[GitHub Docs — Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。

### 标准与工程依据

- CSS 多层 `linear-gradient()` / `repeating-linear-gradient()` 是浏览器原生 `<image>`，无需纹理文件；见 [MDN：Using CSS gradients](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Images/Using_gradients)。
- 如果后续需要切角，`clip-path` 可以把伪元素裁成 `polygon()`；见 [MDN：Introduction to CSS clipping](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Masking/Clipping)。推荐只裁装饰伪元素，不裁真实 button，以免焦点轮廓被切掉。
- Tab 的选中状态与键盘焦点是两件事。横向 tablist 应允许方向键在 tab 间移动，Space/Enter 用于手动激活时的选择；见 [W3C APG Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)。外观改造不得替代或破坏项目现有键盘控制器。
- forced-colors 会移除 `box-shadow`，因此必须提供实边框而不是依赖拟物阴影；见 [MDN：forced-colors](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/forced-colors)。
- `prefers-reduced-motion` 用于移除非必要运动；见 [MDN：prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-reduced-motion)。
- 如有过渡，仅动画 `transform` / `opacity`；不要动画渐变位置、阴影或尺寸。见 [web.dev：High-performance CSS animations](https://web.dev/articles/animations-guide)。

## 当前 MC 边界

现有中间 tab 结构已经合适：

```text
div.center-tabs[role presentation]
└─ div.center-tabs-scroll
   ├─ button#tab-chat.center-tab.active[role=tab][aria-selected=true]
   │  └─ span.center-tab-title.session-tab-title
   └─ button#tab-evaluation.center-tab[role=tab]
      └─ span.center-tab-title
```

`#tab-chat` 的标题还能双击进入会话名编辑，因此不能把标题替换为不可编辑的图片、SVG `<text>`、`content:` 文本或装饰性第二份字符串。狗牌样式必须包容 `.session-summary-input`，编辑时材质仍在，但输入框拥有清楚的实色底和焦点边界。

当前 Rack Lab recipe 已提供足够的一期 token：

- 表面：`--skin-chat-surface`、`--skin-control-background`；
- 壳体/金属暗边：`--skin-shell-border-color`、`--skin-control-border-color`；
- 文字：`--text-primary`、`--text-secondary`；
- 选择与交互：`--accent-primary`、`--accent-primary-hover`、`--accent-primary-active`；
- 焦点：`--focus-ring`；
- 字体：`--skin-label-font`；
- 现有阴影角色：`--skin-control-shadow`、`--skin-control-pressed-shadow`。

因此第一版不应扩展 `SkinContract`。拉丝金属中的明暗值可以在 `rack-lab@1` recipe 内，以 `color-mix()` 从 control surface、shell border、white/black 计算。复杂渐变、孔眼几何和交互选择器属于随包 recipe，不开放为皮肤清单中的任意 CSS 字符串。

如果未来除 Rack Lab 外还有三套以上材质 recipe 真正共享狗牌组件，再评估少量 `--skin-tag-*` color/shadow token；不要为单一试点提前把渐变 stop、孔径、切角坐标或动画时长加入公共合同。

## 推荐视觉结构

### DOM

首版无需修改 HTML：

```html
<button class="center-tab active" role="tab" aria-selected="true">
  <span class="center-tab-title session-tab-title">Chat</span>
</button>
```

装饰分层：

```text
真实 button（透明命中盒、键盘焦点、aria-selected）
├─ ::before（狗牌本体：形状、拉丝、边缘高光、静态投影）
├─ ::after（左侧冲孔与孔边内阴影）
└─ title/input（可见内容，z-index 位于牌体之上）
```

`::before` / `::after` 自动不进入可访问性树，但仍应设 `pointer-events: none`，避免阻挡双击改名、选择文本或指针事件。

### 轮廓

推荐默认形态是横向长圆牌：

- button 命中盒高 44px；牌体高约 32–34px；
- 牌体长宽比随现有 tab 标题伸缩，不锁死图片比例；
- 左端圆角略大于右端，例如左端 10px、右端 6px，让吊孔侧更像牌头，但保持克制；
- 孔眼直径 7px 左右，离左边 10–12px，牌内标题相应增加左内边距；
- 右侧不做历史缺口；它是旧压印机定位特征，并非现代狗牌必要语言；
- 若产品更偏“机架铭牌”而非军牌，可在 `::before` 上用很小的 `clip-path: polygon(...)` 切四角，但不要同时堆叠巨大圆角、尖角、铆钉和链条。

孔眼应看起来是穿透牌体而不是贴上的黑点：孔中心使用 `.center-tabs`/`--skin-chat-surface` 的颜色，外圈用一亮一暗的 inset/outline。它是纯装饰，不承担 selected、unread 或运行状态，也不应变成可点击图标。

### 拉丝金属

牌体建议只用三层静态背景：

1. 一层纵向明暗渐变：上缘窄高光、中间稳定灰、下缘暗边，表达薄板厚度；
2. 一层低不透明度 `repeating-linear-gradient(90deg, …)`，形成横向细拉丝；
3. 一层宽而弱的斜向反光带，让不同宽度仍有金属方向感。

纹理对比必须低。拉丝是材质提示，不应让 11–12px 的 tab 文字出现闪烁边缘。不要使用噪声 PNG、远程纹理、SVG turbulence、`backdrop-filter` 或持续移动的背景位置。

静态高光可以保留一组左上内高光、一组右下暗边和一组小投影。超过 3–4 层 box-shadow 的收益很低，还会让这枚小牌比 composer、panel shell 更“重”。所有 Rack Lab 控件应共享左上亮、右下暗的光源方向。

### 压印文字

“压印”使用现有真实文本叠加极弱的双向 `text-shadow`，而不是复制一份文字：

- 上方 1px 暗边 + 下方 1px 亮边可读成内凹；
- 阴影 alpha 保持很低，避免等宽小字号变糊；
- 文字颜色仍来自 `--text-primary` / `--text-secondary`；选中可使用 `--accent-primary`，但必须分别在 Cream 与 Graphite 的最终金属底色上验证 4.5:1；
- 不使用 `mix-blend-mode`，避免背景变化后对比不可预测；
- 编辑态 `.session-summary-input` 取消 text-shadow，使用实色输入底、明确 border 与 focus。

### 铆钉与孔位

真实狗牌只需一个吊孔。额外四角铆钉会把它变成机架铭牌，也会与 Rack Lab 卡片已有的角部 screw 语言重复。首版不要添加铆钉。

如果用户最终明确选择“机架铭牌”而非“军牌”，可把孔眼改为左右各一枚小沉头螺钉，但这应是互斥方案，不应同时出现吊孔和四铆钉。

## 状态合同

| 状态 | 推荐视觉 | 禁止做法 |
| --- | --- | --- |
| rest / 未选中 | 中性金属；文字使用 `--text-secondary`；静态浅投影 | 仅靠极低对比阴影让它像 disabled |
| hover | 只在 `hover:hover` + `pointer:fine` 下轻微提高上缘高光/文字对比，可抬升最多 1px | 移动扫光、旋转、扩大孔眼、改变选中状态 |
| pressed (`:active`) | 整个真实 button 下压 1px；投影收短、inset 暗边稍强，80–120ms 恢复 | 用 `.active` 选择器误当 pressed；修改 `aria-selected` |
| selected (`aria-selected=true` / `.active`) | 牌体更贴近 panel；文字/一条 2px 内嵌刻线使用 accent；边界更清楚 | 整牌变信号黄；只靠颜色；让 selected 看起来像焦点 |
| focus-visible | 在真实 button 外画至少 2px `--focus-ring` outline，offset 2px；不被牌体裁切 | 把 focus 画在 clipped `::before` 内或仅增加 glow |
| selected + focus | 同时看到选中刻线和焦点 outline | 用同一条黄色边兼任两种状态 |
| editing title | 保留静态牌体；输入框为实色、小圆角、清楚边框；focus 落在 input | 让压印 text-shadow 继续作用于输入文字 |
| disabled（未来） | 仍可辨识文字和边界；移除 hover/pressed；使用 native `disabled` | 只降 opacity 到不可读，或仍显示 pointer 光标 |

当前 screenshot 中 active 标题为信号黄。狗牌化后建议让 accent 只出现在压印标题和窄刻线，不扩大成整块黄色牌体；这样既保留 Rack Lab 识别，又不会与黄色 composer 和 active session 争夺层级。

## 可独立实现的 CSS 草案

以下是 MC 自有结构草案，不复制第三方参数。它说明边界与层级，不是待粘贴的最终视觉值：

```css
body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab {
  --dog-tag-metal: color-mix(in srgb, var(--skin-control-background) 62%, #8b8f96);
  --dog-tag-light: color-mix(in srgb, var(--dog-tag-metal) 68%, white);
  --dog-tag-dark: color-mix(in srgb, var(--dog-tag-metal) 62%, black);
  position: relative;
  isolation: isolate;
  height: 44px;
  padding-inline: 32px 14px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab::before {
  content: "";
  position: absolute;
  z-index: -2;
  inset: 5px 2px 4px;
  border: 1px solid var(--skin-shell-border-color);
  border-radius: 10px 6px 6px 10px;
  background-color: var(--dog-tag-metal);
  background-image:
    linear-gradient(180deg,
      color-mix(in srgb, var(--dog-tag-light) 74%, transparent) 0 8%,
      transparent 28% 70%,
      color-mix(in srgb, var(--dog-tag-dark) 42%, transparent) 92% 100%),
    repeating-linear-gradient(90deg,
      color-mix(in srgb, white 4%, transparent) 0 1px,
      color-mix(in srgb, black 3%, transparent) 1px 3px),
    linear-gradient(104deg,
      var(--dog-tag-dark), var(--dog-tag-metal) 24%,
      var(--dog-tag-light) 48%, var(--dog-tag-metal) 68%, var(--dog-tag-dark));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 40%, transparent),
    inset 0 -1px 0 color-mix(in srgb, black 34%, transparent),
    0 2px 3px color-mix(in srgb, black 28%, transparent);
  pointer-events: none;
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab::after {
  content: "";
  position: absolute;
  z-index: -1;
  left: 12px;
  top: 50%;
  width: 7px;
  height: 7px;
  translate: 0 -50%;
  border: 1px solid var(--dog-tag-dark);
  border-radius: 50%;
  background: var(--skin-chat-surface);
  box-shadow:
    inset 1px 1px 1px color-mix(in srgb, black 48%, transparent),
    0 0 0 1px color-mix(in srgb, var(--dog-tag-light) 54%, transparent);
  pointer-events: none;
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab-title {
  position: relative;
  z-index: 1;
  text-shadow:
    0 -1px 0 color-mix(in srgb, black 24%, transparent),
    0 1px 0 color-mix(in srgb, white 20%, transparent);
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
  .center-tab[aria-selected="true"] {
  color: var(--accent-primary);
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
  .center-tab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

@media (hover: hover) and (pointer: fine) {
  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
    .center-tab:hover:not(:disabled) {
    translate: 0 -1px;
  }
}

body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
  .center-tab:active:not(:disabled) {
  translate: 0 1px;
}
```

实现时应把过渡精确限定为 `translate`、必要的 `color`；不要 `transition: all`。背景渐变和 box-shadow 保持静态，不在 hover/pressed 间动画。最终数值需在浏览器内按真实 42/44px tabbar 调整，尤其检查孔眼、标题基线和 active tab 与 panel 边界的衔接。

## 窄屏与长标题

- 保留 `.center-tabs-scroll` 的横向滚动和 `.center-tab-title` 的 ellipsis，不改 tab 数据结构。
- `#tab-chat` 仍允许较长的动态 session title；狗牌不可固定为 2:1 图片比例。
- `<= 640px` 时命中盒至少 44px 高，牌体可以降到约 31px；左右 padding 可缩为 `28px 12px`，孔径缩至 6px，但不能取消可识别焦点。
- 标签最大宽度应基于可用 viewport，例如 `max-width: min(280px, 62vw)`；当前 `42vw` 若在实际两 tab 场景中更稳定，可继续保留。必须用中英文长标题、200% 文本缩放和浏览器最小宽度验收。
- 粗指针没有 hover 反馈也能看懂 rest/selected；tap 命中区属于真实 button，不是 32px 的伪元素牌体。
- 横向滚动时焦点 outline 不应被 `.center-tabs-scroll` 顶/底裁切；必要时给 scroll 容器预留 2–3px 内边距，而不是去掉 outline。

## 键盘、ARIA 与编辑态

- 保持 `button type="button" role="tab"`、`aria-controls`、`aria-selected` 和 panel 关系不变；不把 tab 改成 `<div>`、checkbox 或链接。
- 现有 tab 控制器应继续负责 Left/Right、Space/Enter、roving tabindex 或项目已有等价行为；CSS 不新增键盘监听。
- selected 是 `aria-selected`，pressed 是按下瞬间的 `:active`，focus 是 `:focus-visible`；三者必须能重叠且视觉互不覆盖。
- `title="Chat\nDouble-click to edit session name"` 及双击改名功能不应被伪元素阻挡。所有装饰层 `pointer-events: none`。
- `.session-summary-input` 出现后仍要位于牌体上方，宽度随标题区域收缩，Esc/Enter/blur 行为不变；input 的 focus ring优先于外层 tab ring。
- 狗牌孔、刮痕、铆钉和高光不增加 `aria-label`、title 或 live region；它们不是信息。

## Forced colors

在 forced-colors 中，渐变、阴影和伪孔都不是可靠的状态载体。直接退化为系统色 tab：

```css
@media (forced-colors: active) {
  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab {
    border: 2px solid ButtonText;
    background: Canvas;
    color: ButtonText;
    forced-color-adjust: auto;
  }

  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
    .center-tab[aria-selected="true"] {
    border-color: Highlight;
    background: Highlight;
    color: HighlightText;
  }

  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
    .center-tab::before,
  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
    .center-tab::after {
    display: none;
  }

  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"]
    .center-tab:focus-visible {
    outline: 2px solid CanvasText;
    outline-offset: 2px;
  }
}
```

不要用 `forced-color-adjust: none` 强保低对比金属外观。系统高对比模式下，识别 tab、selected 和 focus 比保留狗牌质感更重要。

## Reduced motion

该方案默认没有循环动画，因此 reduced-motion 成本很低：

- 禁止常驻扫光、浮动、晃动、链条摆动或循环反射；
- `prefers-reduced-motion: reduce` 下移除 hover 抬升、pressed 下压及所有 transition；
- 静态拉丝、孔眼、压印和 selected 刻线可保留，因为它们不运动；
- 应同时尊重 MC 应用内“装饰动效关闭”偏好；任一来源要求 reduce 时都使用静态状态。

```css
@media (prefers-reduced-motion: reduce) {
  body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] .center-tab {
    transition: none;
    translate: none !important;
  }
}
```

## 性能预算

- 每个 tab 最多两个伪元素，无额外 DOM、图片或 SVG filter；仅使用现有局部 pointer listener 更新有限 CSS 变量。
- 最多三层静态 background image 和三到四层静态 shadow；材质不随帧更新。
- hover/pressed 最多过渡 `translate` 和 `color`；不动画 `background-position`、gradient stops、filter、blur、box-shadow、width、height 或 padding。
- 不长期设置 `will-change`。这个小组件没有理由为静态金属层常驻新合成层。
- 不使用 `backdrop-filter`、mix-blend-mode、noise canvas 或 GSAP；`pointermove` 只绑定 tablist，离开即复位，粗指针与 reduced-motion 使用静态回退。
- tab 数量增多时，横向滚动性能应接近现有实现；在 Chrome DevTools Paint Flashing 下，静止时没有持续 repaint，按压时不重绘整个 chat panel。

## Recipe 与 token 边界

一期实现应继续落在已登记的 `rack-lab@1`：

```text
Skin manifest: styleRecipe = rack-lab@1
→ ThemeManager 写入受控 data 属性
→ 随应用打包的 skins.css 选择器
→ 仅消费 SkinContract 已允许 token
```

皮肤清单不得提供：

- 任意 gradient 文本、clip-path、选择器或伪元素内容；
- 远程纹理、data URL、SVG、字体或脚本；
- 孔眼位置、切角坐标、动画关键帧或 `transition: all`；
- 通过材质 recipe 改写 `role=tab`、ARIA、DOM 顺序、双击编辑或 panel 激活行为。

如果将来抽成独立 `metal-dog-tag-tabs@1` recipe，它仍必须由 MC 随包、登记组件白名单、版本化并带四主题/辅助模式回归；不能成为运行时 CSS 下载器。

## 实施验收清单

- [ ] `#tab-chat` 仍是原生 button tab，`aria-selected`、`aria-controls`、tabpanel 与键盘行为未变；
- [ ] 狗牌使用 MC 自有 CSS，无下载图片、未知许可 SVG、远程纹理或新增依赖；
- [ ] Rounded/oblong、单孔、静态拉丝和压印四个特征清楚，但没有链条、个人信息、历史缺口堆叠或四角铆钉；
- [ ] `::before` / `::after` 不截获指针，双击改名与 `.session-summary-input` 正常；
- [ ] rest、hover、pressed、selected、focus-visible、editing、disabled 各状态不混淆；
- [ ] active tab 不只靠颜色，focus 也不只靠 glow；
- [ ] Default Dark/Light 不受影响，Rack Lab Cream/Graphite 的文字对比、边界与孔眼均清楚；
- [ ] active 文本在最终金属底上达到普通小字 4.5:1；focus 与非文字状态边界达到 3:1；
- [ ] `<= 900px`、`<= 640px`、长中英文标题、200% 文本缩放与横向滚动无裁切；
- [ ] coarse pointer 不依赖 hover，真实命中盒至少 44px 高；
- [ ] forced-colors 使用系统实色和边框，狗牌伪元素不遮蔽 selected/focus；
- [ ] reduced-motion 与应用内 motion-off 下没有位移或循环动画；
- [ ] 静止时无持续 repaint，没有 `transition: all`、`will-change`、filter 或背景扫光；
- [ ] 相关 tab/会话命名测试、style recipe CSS 测试、完整 Node 测试和 production build 通过；
- [ ] 浏览器视觉验收分别覆盖 selected、keyboard focus、selected+focus、editing 和窄屏。

## 推荐实施顺序

1. 不加动效，先用 `::before`/`::after` 完成长圆牌、孔眼、拉丝和压印，并确保双击编辑无回归。
2. 用 `aria-selected` 完成中性/选中两态，随后补 `:focus-visible`，单独验收 selected + focus。
3. 增加 forced-colors 与 reduced-motion 静态回退。
4. 最后才加入 fine-pointer hover 和 1px pressed 位移；如果视觉收益不明显，宁可保持完全静态。
5. 在 Rack Lab Cream/Graphite、桌面/窄屏完成截图对比后，再决定是否把同一语言扩展到 Evaluation 等其他中间 tab。
