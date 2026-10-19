---
name: AI Library Neural Reference
description: Neural Vault reference adaptation; user presentation review remains open.
colors:
  space: "#070708"
  panel: "#141417"
  ink: "#eeeef1"
  muted: "#a1a1aa"
  gold: "#f4c069"
  line: "#343439"
rounded:
  control: "20px"
  directory: "15px"
  dialog: "16px"
spacing:
  compact: "8px"
  control-gap: "12px"
  desktop-edge: "22px"
---

## Source and acceptance

The user specified Neural Vault, https://github.com/useroneoneone/neural-vault, revision 842fd9600e4c6d930005cf3688b9a68da238b1c6. Its locally rendered preview and source informed v1.8. Source notices and license are under frontend/vendor/neural-vault/. Current implementation is frontend/css/neural.css, frontend/js/neural-visuals.js and the existing universe controller/scene.

The implemented reference adaptation is TESTED for the recorded functional scenarios. The user says the overall presentation is still not ideal: visual and interaction acceptance is REVIEW_REQUIRED. Do not treat current composition as final user approval. Next direction awaits clarification.

## Visual grammar

Black spatial canvas with a faint grid. Topics are colored wireframe jellyfish; a larger warm wireframe core opens all Notes in the current scope. Small luminous nodes show actual topic membership. Source colors differentiate groups; captions retain content types. Neither distance nor navigation lines imply verified semantic relationships.

Preserve real 3D rotation and zoom. Topic frames sit around the core, with a right-side legend on desktop. Topic labels use count, name and actual kind; they are focusable buttons. Label collision handling hides crowded labels, with a complete directory as the equivalent navigation path. Mobile omits the right legend and uses the directory.

## Interface and reading

Light floating brand/actions, central navigation, bottom search with Ctrl+K, explicit exit and pause/reset. The removed Notes/Memory type switch stays removed. Topic selection opens the directory. Notes, Memory, Skills and maintenance keep their distinct data and editing rules.

Desktop Notes and Memory appear in right readers; mobile readers fill the viewport. Existing source and revision controls remain. Topic membership uses persistent references, never content copies. The local Skill catalogue only reads metadata and locations and does not execute skill instructions.

## Motion and responsive behavior

Camera transitions retain the interruptible 650ms implementation. Topic breathing is subtle and stops during reading, hover, selected state, pause, interaction, page hiding and reduced motion. Content remains readable when motion is disabled. Compact styles switch at 1100px; the mobile navigation/reader layout switches at 760px. Actual checks cover 1440, 612 and 390px widths; low-end physical devices and large-scale performance remain unverified.

## Spider mecha visual v1.1

2026-10-12 用户明确选择星图原生探针：悬浮暖白核心、薄片护壳、纤细弧形八足。七个应用以护瓣、轨道环、双核、圆润种子、流线尾鳍、六瓣护框、观测拱等结构区别，不仅换色。小范围应用识别光配暖金轮廓，体量收敛以减少行星遮挡。继承本页星图语汇，不改变其他表面的视觉身份与功能。

模型已实现并经过桌面/手机限定QA；视觉方向USER APPROVED，最终模型视觉USER_ACCEPTED待确认。源码、恢复点和证据见 work/SPIDER-MECHA-VISUAL-v1.1.md。

## Data and interaction boundaries

Do not add demo records, synthetic links, fictitious heatmaps, unsupported counts or unimplemented Insight controls to match the reference. Maintain scope, drafts, provenance, candidate review and revision protections. Do not propagate legacy small metadata as a mandatory accessibility scale. Functional test success does not establish the user's visual acceptance.
