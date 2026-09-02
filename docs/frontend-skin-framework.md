# MatCreator frontend skin framework

Status: MVP implemented on `codex/pr251-preview-20260828`\
Scope: visual presentation in `web/vite-frontend`; no workflow, materials-science, backend, or Remote Job semantics

## Decision

MatCreator skins are **validated data packages**, not downloaded stylesheets or JavaScript plugins. A skin may choose from an allow-listed set of semantic CSS custom properties, declare one or more variants, and reference a style recipe that is already compiled into MatCreator. MatCreator retains ownership of selectors, component structure, interaction, responsive behavior, accessibility, and scientific/domain colors.

This separation is the basis for a future skin store:

```text
manifest JSON
→ SkinContract validation
→ resolve styleRecipe (default: standard@1)
→ read-only StyleRecipeRegistry availability check
→ immutable ThemeRegistry snapshot
→ ThemeManager atomic apply + persistence
→ body data attributes and semantic tokens
→ statically bundled MatCreator-owned recipe CSS / canvas adapters
```

The MVP ships two built-in skins:

- `matcreator-default`: the existing restrained scientific workspace, in Dark and Light variants.
- `rack-lab`: an adaptation of modular synthesizer hardware, in Cream and Graphite variants. It uses enamel/graphite panels, signal yellow, strong outlines, tactile control shadows, condensed labels, and decorative corner fasteners without turning ordinary UI controls into literal audio knobs.

The framework therefore has two store-facing levels:

1. **Token-only skin:** safe catalog data selects `standard@1` and changes only validated semantic variables. This is the default public skin-store capability.
2. **MC-reviewed style recipe:** a skin may opt into a named, versioned selector/effect bundle that has been reviewed, tested, and shipped with MatCreator. A store manifest can reference such a recipe, but cannot install recipe code.

## Goals and non-goals

Goals:

- Change visual language without changing DOM behavior or application state semantics.
- Preserve PR #251 streaming, transcript/session virtualization, timeline, skills, graph, viewer, and evaluation behavior.
- Apply a skin immediately without reloading, remounting, or reconnecting stateful components.
- Keep the existing binary light/dark bridge working while allowing variants with arbitrary names.
- Make future installation safe enough that a store does not need to grant a theme arbitrary CSS or script execution.
- Let multiple skin identities reuse a reviewed component treatment without coupling CSS selectors to one skin ID.

Non-goals for the MVP:

- Remote download, purchase, entitlement, signing, or account sync.
- Arbitrary component layouts, HTML fragments, selectors, fonts, images, or executable extensions.
- Runtime CSS text, local or remote CSS paths, stylesheet URLs, `@import`, or script-driven recipe registration.
- Recoloring scientific semantics such as element CPK colors, crystal axes, lifecycle states, graph status meaning, warnings, or errors.
- Remounting Ketcher, Three.js, vis-network, xterm, or virtualized transcript nodes when a skin changes.

### Current design-scope rule

Unless a design request explicitly says **global**, new materials, component shapes, decorative treatments, motion, and interaction feel must be scoped to the explicitly selected skin. Shared changes are limited to non-skin invariants such as truthful backend data projection, accessibility semantics, security, and layout defects that hide or clip functional content; those exceptions must be called out in the implementation log.

## Manifest contract

A manifest has a stable ID, semantic version, schema version, a default variant, preview swatches, and one or more variants. Variant identity and browser color scheme are deliberately separate. Schema v1 also accepts an optional `styleRecipe` object containing exactly `id` and `apiVersion`. Omitting it normalizes the registered manifest to `{ id: "standard", apiVersion: 1 }`; built-in manifests declare the field explicitly so their visual dependency is reviewable in source.

```js
{
  id: "rack-lab",
  name: "Rack Lab",
  version: "0.1.0",
  skinSchemaVersion: 1,
  styleRecipe: { id: "rack-lab", apiVersion: 1 },
  defaultVariant: "light",
  preview: { swatches: ["#f4f3ee", "#ffc400", "#343640"] },
  variants: {
    light: {
      label: "Cream",
      colorScheme: "light",
      tokens: {
        "--bg-canvas": "#e8e7e1",
        "--bg-surface": "#f4f3ee",
        "--bg-elevated": "#fffdf8",
        "--text-primary": "#17191f",
        "--text-secondary": "#55575e",
        "--border-default": "#1b1d22",
        "--accent-primary": "#8a6500",
        "--focus-ring": "#7767ff",
        "--success": "#2e7b58",
        "--warning": "#b87500",
        "--danger": "#b9444d"
      }
    }
  }
}
```

The validator rejects:

- unsupported schema versions, malformed IDs/versions, missing variants, or missing required semantic tokens;
- malformed recipe IDs/versions and any `styleRecipe` field other than `id` and `apiVersion`;
- token names outside the explicit schema;
- `url()`, `@import`, `expression()`, `javascript:`, `data:`, declarations, selector braces, and other unsafe value syntax;
- values whose type does not match the token contract.

Contract validation establishes that the reference is well formed; it does not make arbitrary recipe IDs available. `ThemeRegistry` additionally requires the exact `id@apiVersion` to exist in the read-only `StyleRecipeRegistry`, then stores a deep-frozen, normalized snapshot. It rejects duplicate skin IDs and unavailable recipes and never returns a mutable live manifest. This prevents a package from mutating its token set after it crosses the validation boundary or naming CSS that was not prepackaged with MatCreator.

## Token layers

The framework separates token intent from component selectors.

### Foundation semantics

- Canvas/surfaces: `--bg-canvas`, `--bg-surface`, `--bg-elevated`, hover/active surfaces.
- Text: primary, secondary, muted, and disabled.
- Borders: subtle, default, and strong.
- Accent: primary states, secondary accent, selection, focus ring, and on-accent foreground.
- Feedback: success, warning, danger, and info.
- Specialized semantic surfaces: terminal, sessions, timeline badges, message bubbles, and step-feed states.

### Skin structure

- Shell surface, border, radius, and elevation.
- Per-role graph/chat/context/composer surfaces.
- Graph-surface tone (`--skin-graph-surface-tone: light | dark`) for Canvas renderers whose local panel contrast can differ from the page color scheme.
- Tactile control background, border, resting shadow, and pressed shadow.
- Decorative fastener color/opacity.
- Display and label font stacks, letter spacing, and label transform.

### Style recipe layer

A style recipe is a versioned capability name, not a stylesheet payload. `standard@1` uses the universal MatCreator component treatment. `rack-lab@1` activates the reviewed instrument-panel selectors and effects. The API version identifies the selector/behavior contract independently of the skin package version, so recipe evolution can be explicit instead of silently changing every dependent skin.

Recipe CSS is imported by the application and emitted as part of the Vite production bundle. The runtime exposes no loader for CSS text, a filesystem path, a URL, `@import`, HTML, or JavaScript. `StyleRecipeRegistry` is deliberately read-only: it offers lookup/list operations and has no public `register()` method. Adding or upgrading a recipe is therefore a normal MatCreator source change with review, tests, production build, and visual acceptance—not a skin-store install action.

Recipe selectors key off both runtime attributes, for example:

```css
body[data-style-recipe="rack-lab"][data-style-recipe-version="1"] { /* ... */ }
```

Base recipe rules do not key off `data-skin`. Rack Lab is already migrated to this boundary, so another validated skin may reuse the same reviewed recipe while supplying different tokens and metadata. Skin identity remains available through `data-skin` for diagnostics and catalog UI.

Recipe capabilities are not limited to CSS selectors. The Agent Graph is a `vis-network` Canvas and therefore cannot consume a CSS droplet selector. Its renderer resolves a small, read-only graph recipe module from the same reviewed `id@version`: `standard@1` owns the circle fallback, while `rack-lab@1` owns the MC-created Bézier droplet and its liquid alpha recipe. Unknown recipe IDs or versions resolve to `standard@1`; no Rack Lab colour, opacity, or geometry default leaks into them. Adding or deleting a Canvas capability is consequently limited to one recipe module and its tests, without rebuilding the Network or its DataSets. Node type continues to own the face color and glyph, while lifecycle status remains a separate symbol badge or running aura. Geometry, hit dimensions, status anchors, vine endpoints, and redraw behavior share one pure geometry contract.

### Legacy aliases

Existing selectors still consume `--bg`, `--panel`, `--text`, `--muted`, `--accent`, `--border`, and RGB triplets. `base.css` continues to derive those aliases from semantic tokens. New component work should use the semantic names; aliases exist only to make migration incremental.

## Runtime responsibilities

### `SkinContract.js`

Defines the allow list, required tokens, value types, manifest validation boundary, and `styleRecipe` normalization. It owns data-shape security and schema compatibility, not rendering or recipe availability.

### `StyleRecipeRegistry.js`

Contains immutable metadata for the style recipes compiled into this MatCreator build. Its public object is read-only and intentionally has no dynamic registration or code-loading path.

### Canvas recipe adapters

Canvas components keep their recipe-specific behavior in adjacent, read-only adapter modules rather than in shared renderer defaults. For example, `features/graphs/agentGraphRecipes.js` owns the `standard@1` circle fallback and the complete `rack-lab@1` droplet/liquid recipe. `AgentGraphView` only resolves and applies the selected adapter. This keeps adding, changing, or removing one recipe local to its module and avoids one skin's opacity, geometry, or effect defaults changing another skin.

### `ThemeRegistry.js`

Registers validated immutable skin snapshots and exposes stable lookup/list operations. Before registration it resolves the schema-v1 default and rejects any recipe absent from the prepackaged `StyleRecipeRegistry`. A future store installer should call this boundary only after package verification and local persistence; registration does not install CSS.

### `ThemeManager.js`

Resolves a requested skin/variant, atomically applies tokens to `<body>`, writes `data-skin`, `data-style-recipe`, `data-style-recipe-version`, `data-variant`, `data-theme`, and `color-scheme`, persists the user selection, and then notifies consumers. If DOM or storage application fails, it restores the previous DOM/token state and suppresses change events.

Persistence keys:

- `mat_skin`: selected skin ID.
- `mat_skin_variant`: selected variant ID.
- `mat_theme`: mirrored legacy `light | dark` color scheme.

There is intentionally no recipe storage key. Recipe ID/version are derived from the immutable registered skin on every apply, so stale or user-edited local storage cannot select a structural CSS capability independently of its skin.

Compatibility events:

- `matcreator-theme-change`: legacy detail remains exactly `"light"` or `"dark"`.
- `matcreator-skin-change`: structured selection with skin ID/name/version, `styleRecipeId`, `styleRecipeApiVersion`, variant, and color scheme.

Subscribers run before the window events so the shared `state.theme` bridge is current when existing graph/viewer listeners synchronously read it.

### `AppearanceController.js`

Renders Settings → Appearance from registry data using native radio inputs, visible selection markers, preview swatches, an `aria-live` status, and responsive touch targets. Skin/variant changes apply and persist immediately. They are intentionally excluded from Settings' backend `PUT` and restart flow; the generic Save action is hidden while the Appearance pane is active.

### `skins.css`

MatCreator-owned selectors translate the structural tokens into real component appearance. The Rack Lab rules alter shell texture, panel roles, control tactility, labels, sessions, message surfaces, graph texture, and composer hierarchy under its recipe ID/version attributes. The manifest itself never supplies selectors, and every recipe stylesheet remains static input to Vite.

## Migrating third-party CSS inspiration

Third-party examples are design references, not automatically installable skins. Before any effect enters a reviewed recipe:

1. **Provenance and license:** record the original URL, author, license text/version, retrieval date, and whether modification and redistribution are permitted. Preserve required notices. Unknown or incompatible licensing means visual-study only and an independent implementation; no source copying or bundled asset reuse.
2. **Effect inventory:** isolate the exact visual idea and identify its HTML assumptions, pseudo-elements, JavaScript, pointer tracking, external images/fonts, filters, and browser-specific features. Reject hidden network/runtime dependencies.
3. **MC-owned rewrite and scope:** adapt the idea to existing semantic markup and place it under one exact `data-style-recipe` plus version selector. Never introduce global element rules, unscoped custom-property names, `transition: all`, or selectors that depend on a third-party demo DOM.
4. **Token extraction:** replace hard-coded themeable colors, borders, radii, shadows, type treatments, and opacity with approved semantic or skin-structure tokens. Scientific colors and lifecycle meaning stay outside recipe control.
5. **Interaction and layout review:** map hover, active, selected, disabled, loading, error, and keyboard-focus states to existing MC semantics. Verify desktop and narrow layouts, touch target size, overflow, text expansion, and that decoration cannot intercept clicks.
6. **Motion and accessibility:** provide a complete `prefers-reduced-motion: reduce` path, avoid essential meaning in animation/color alone, preserve visible `:focus-visible`, and check contrast, forced/limited motion behavior, screen-reader labeling, and keyboard reachability.
7. **Performance and acceptance:** bound blur/filter/shadow cost and animated area, avoid perpetual high-cost effects on large surfaces, then run Node tests, the Vite production build, and desktop/narrow browser visual acceptance before adding the recipe to the read-only registry.

This process permits careful adaptation of MIT or otherwise compatible source when its obligations are met, while keeping GPL-only or unknown-license examples as conceptual references unless MatCreator's distribution decision explicitly changes.

## Stateful renderer compatibility

Skin changes must not reconstruct behavior-bearing surfaces.

- CSS/DOM components resolve semantic variables automatically.
- Agent/skill graphs retain the legacy light/dark event bridge. Agent Graph also reads the validated graph-surface tone, redraws immediately and once on the following frame when a recipe changes, and never reconstructs its Network or DataSets.
- Execution Plan Canvas must redraw after theme/skin changes without replacing its network or data sets.
- xterm updates `terminal.options.theme` from computed semantic tokens without reconnecting its WebSocket.
- Structure Viewer keeps scientific colors fixed and follows the light/dark bridge for scene contrast.
- Ketcher remains a fixed-light vendor island in this MVP; its outer dialog follows the active skin, but the editor is never remounted solely for appearance.
- Virtualized chat/session nodes are not recreated or fully remeasured because this contract does not permit changing body typography metrics. If a future schema permits metric-changing typography, the virtualizers must invalidate measurements while preserving the reading anchor.

## Responsive and accessibility contract

- Desktop skins may change surface treatment but not the three-region ownership or resize behavior.
- At compact widths, existing rail caps and overflow guards remain authoritative.
- At `<= 900px`, chat remains first and secondary regions remain height-bounded with their own scroll containers.
- At `<= 640px`, shells become edge-to-edge and skin controls use at least 44px targets.
- Native form controls, textual selection, `:focus-visible`, non-color status wording, and `prefers-reduced-motion` behavior remain mandatory.
- A skin cannot remove focus, hide labels, redefine lifecycle terms, or make status meaning color-only.

## Future skin-store boundary

The next store-facing layer should add package concerns without weakening the runtime contract:

```text
catalog metadata
→ download to quarantine
→ size/hash/signature and publisher verification
→ parse JSON only
→ SkinContract validation
→ require standard@1 or another recipe already present in this MC build
→ copy into versioned local package storage
→ ThemeRegistry registration
→ preview/apply/rollback
```

Recommended package additions:

- publisher ID, license, compatibility range, content hash, signature, localized copy, and preview assets;
- explicit install/update/uninstall state separate from the active selection;
- cached last-known-good version and rollback after load failure;
- store previews rendered by MatCreator from swatches/tokens, not package HTML;
- accessibility linting for contrast and focus tokens before publication.

The public catalog should default to token-only skins on `standard@1`. A skin that references an MC-reviewed recipe remains a data package and can be listed only for compatible MatCreator builds; the recipe code ships with the application, never with the store download. Arbitrary CSS text, paths, URLs, `@import`, scripts, remote fonts, and selector overrides remain outside the skin format. If deeper layout extensibility is later required, it should be a separately permissioned plugin capability rather than a skin-store feature.

## Quality gates

For each skin batch:

- validate every built-in manifest;
- test initialization, schema-v1 default recipe normalization, unavailable-recipe rejection, persistence boundaries, recipe attributes/events, legacy migration, fallback, apply failure rollback, Appearance interaction, and teardown;
- assert recipe selectors are version-scoped and no Rack Lab structural selector depends on `data-skin="rack-lab"`;
- run the full Node test suite and Vite production build;
- visually inspect Default Dark/Light and Rack Lab Cream/Graphite at desktop and narrow viewports;
- verify no page-level horizontal overflow, no new console errors, visible focus, readable contrast, and no remount/reconnect of stateful renderers.
