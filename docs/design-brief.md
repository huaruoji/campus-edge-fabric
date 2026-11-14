# 前端设计简报（v1，执行用）

定位：**工程控制台**。信息密度优先，装饰最少。目标用户是队友和评委，5 秒内看懂「谁在算、算了什么、结果在哪」。

## 1. 明确不要做的事（这些是 AI 生成的典型特征）

- 渐变背景、发光边框、玻璃拟态
- 所有块统一大圆角 + 同一个灰色阴影（层级靠留白和边框区分，不靠阴影堆叠）
- 全大写小标签（eyebrow）
- 用中点符号把若干 meta 串成一行（如 `a · b · c`）；改成表格列或分行
- 装饰性 emoji、彩色标签泛滥
- 每块内容都切成卡片（只有需要分组的才用卡片）
- 页面加载时整页 fade-in、卡片逐个滑入

## 2. Token（唯一允许使用的变量名，禁止硬编码颜色）

~~~css
:root{
  color-scheme: light;
  --gray-25:#FCFCFD; --gray-50:#F7F8FA; --gray-100:#F1F3F6; --gray-200:#E4E7EC;
  --gray-300:#D0D5DD; --gray-400:#98A2B3; --gray-500:#7C8AA0; --gray-600:#667085;
  --gray-800:#344054; --gray-900:#101828;
  --blue-50:#EFF4FF; --blue-100:#DBE7FF; --blue-600:#2563EB; --blue-700:#1D4ED8;
  --green-600:#079455; --green-700:#067647; --green-50:#ECFDF3;
  --amber-600:#DC6803; --amber-700:#B54708; --amber-50:#FFFAEB;
  --red-600:#D92D20; --red-700:#B42318; --red-50:#FEF3F2;

  --canvas:var(--gray-50); --surface:#FFFFFF; --surface-subtle:var(--gray-25);
  --surface-hover:var(--gray-100);
  --border:var(--gray-200); --border-strong:var(--gray-300); --border-focus:var(--blue-600);
  --text:var(--gray-900); --text-muted:var(--gray-600); --text-faint:var(--gray-400);
  --accent:var(--blue-600); --accent-hover:var(--blue-700); --accent-soft:var(--blue-50);
  --ok:var(--green-700); --ok-soft:var(--green-50);
  --warn:var(--amber-700); --warn-soft:var(--amber-50);
  --err:var(--red-700); --err-soft:var(--red-50);

  --radius-card:12px; --radius-control:8px; --radius-pill:999px;
  --shadow-card:0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08);
  --shadow-pop:0 8px 24px rgba(16,24,40,.12);
  --font-sans:"Inter","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
~~~

## 3. 布局

~~~
+----------------------+-------------------------------------------+
| 侧栏 220px（固定）    |  内容区（max-width 1200，左右 padding 32） |
|  Logo + 版本          |  页头：标题 + 一行说明 + 右侧操作按钮      |
|  导航：设备/任务/对话 |  正文区块                                 |
|  底部：连接状态        |                                           |
+----------------------+-------------------------------------------+
~~~

- 断点：<960px 侧栏折叠成顶部横向 tab；<640px padding 16。
- 间距只用 4 的倍数：4/8/12/16/24/32。
- 表格行高 44，表头 36，粘性表头。

## 4. 字体与数字

- 字号：12（辅助）/13（表格与正文）/14（正文主）/16（区块标题）/20（页面标题）；行高 1.5。
- 所有数字列、耗时、ID、吞吐：`font-variant-numeric: tabular-nums`。
- ID 一律 mono + 12px + `--text-muted`，并支持点击复制。

## 5. 组件规则

- 按钮：主按钮填充 accent；次按钮白底 + 边框；危险操作 `--err` 描边、二次确认。高度 32/36。
- 输入：白底、1px `--border`、focus 用 `--border-focus` + 3px `--accent-soft` 外环（不要去掉 outline 不补）。
- 卡片：仅用于分组。白底 + 1px `--border` + `--radius-card` + `--shadow-card`。
- 状态徽标：软底 + 深色文字（ok/warn/err/accent 四套），**必须带文字**，不能只靠颜色。
- 进度：轨道 `--gray-100`，填充按状态取语义色，高度 6，圆角 pill。
- 指标条（CPU/内存/GPU）：标签 12px `--text-muted`，条高 6，数值右对齐 tabular-nums。
- 空状态：标题 14 加粗 + 一行说明 + 一个明确动作按钮，居中，上下留白 48。
- 加载：骨架条（`--gray-100`，`prefers-reduced-motion` 下静止），不要转圈超过 3 处。

## 6. 可访问性硬性要求（Vercel Web Interface Guidelines）

- 图标按钮必须有 `aria-label`；表单控件必须有 `<label>` 或 `aria-label`。
- `:focus-visible` 可见，禁止 `outline:none` 无替代。
- 异步更新（提交完成、任务状态变化）用 `aria-live="polite"`。
- 图片写死 `width`/`height`；折叠区外的图片 `loading="lazy"`。
- 尊重 `prefers-reduced-motion`；只动画 `transform`/`opacity`，禁止 `transition: all`。
- 交互元素用 `<button>`/`<a>`，不要 `<div onClick>`。
- 正文对比度 ≥4.5:1（`--text-faint` 只用于非关键文字）。

## 7. 中文文案

- 句子用中文全角标点；省略号用 `…`；数字与单位间不空格（如 512 px 写成 512px）。
- 状态词统一：排队中 / 运行中 / 已完成 / 部分失败 / 失败 / 已取消。
- 错误提示写清「发生了什么 + 下一步做什么」。

## 8. 文件分工（**不要越界改文件**）

| 角色 | 拥有文件 |
|---|---|
| F1 主题 | web/index.html、web/src/main.tsx、web/src/App.tsx、web/src/styles.css（拆成 tokens.css/base.css/layout.css/components.css）、ui.tsx、ConnectionBar.tsx、StatsStrip.tsx、JobList.tsx、ErrorBoundary.tsx |
| F2 功能 | JobChat.tsx、presets.ts（新建）、JobDetail.tsx、WorkerPanel.tsx、api.ts、usePolling.ts、chat.css |
| Lead 后端 | packages/**、docs/api.md |

任何人**不要 git commit / push**，Lead 统一提交。改完必须 `cd web && npx tsc --noEmit && npm run build` 通过。

## 9. 完成定义

- 浅色主题全站生效，无残留深色变量；无硬编码颜色。
- 375 / 768 / 1440 三档宽度无横向滚动。
- 键盘可完成「填 token → 看设备 → 开对话 → 提交 → 看详情」。
- build 产物落在 `packages/cef-coordinator/src/cef_coordinator/web/`。