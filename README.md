# ETF RPS Site

纯静态 ETF RPS 日报站点。首页是固定入口，用户不需要记住很长的日报 HTML 文件名。

## 目录结构

```text
etf-rps-site/
├─ index.html
├─ data/
│  └─ latest.json
├─ reports/
│  └─ daily_observation_full_2026-06-12.html
├─ assets/
└─ README.md
```

## 访问逻辑

```text
今日 ETF RPS 观察
↓
点击查看最新日报
↓
历史日报列表
```

## 部署建议

建议使用：

```text
GitHub 存代码
↓
Vercel 部署访问
↓
绑定自定义域名
```

例如后续可以绑定：

```text
etf.fengyaodong.com
```

先不要做复杂后端。后续如果需要每日自动更新、API、Cron 定时任务，再逐步接入 Vercel Functions 或独立服务。

## Vercel Web Analytics

Vercel 按 `vercel.json` 执行 `node scripts/build_vercel_site.mjs`，将现有的首页、回测页、payload、assets、data 和 reports 复制到 `public/`，并给每个 HTML 注入官方 Web Analytics 脚本。构建不拉取行情、不改写源页面；每日更新后仍会自动注入。

运行验证：

```bash
node --test tests/test_build_vercel_site.mjs
node scripts/build_vercel_site.mjs
```

需在目标 Vercel 项目启用 Web Analytics，并让需要统计的域名由该项目提供服务。GitHub Pages 不提供 `/_vercel/insights/` 接口；仅合并代码无法统计仍指向 Pages 的域名。验证时应检查真实浏览器的上报请求和 Analytics 后台接收结果，构建成功或脚本返回 200 并不代表统计已入库。

## 本地预览

在仓库根目录启动静态服务：

```bash
python3 -m http.server 8080
```

访问：

```text
http://127.0.0.1:8080/etf-rps-site/
```
