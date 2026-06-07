# 性能采集说明

站点内置轻量性能采集，用于线上运行一段时间后把数据拷回本地分析。默认写入本机 JSONL 文件，不依赖第三方服务。

## 采集内容

- 后端请求：慢请求、5xx、少量抽样请求，包含路径、状态码、耗时、请求 ID、用户标识。
- 浏览器：页面加载指标、慢 API、失败 API、长任务、布局抖动、最大内容绘制、JS 异常、静态资源加载失败。
- 不采集请求体、答案、密码、token、cookie 等敏感字段；URL 查询参数中敏感 key 会脱敏。

## 文件位置

默认每天一个文件：

```text
user_data_simple/_shared/performance/perf-YYYY-MM-DD.jsonl
```

每行是一个独立 JSON 对象，适合直接用 `jq`、Python 或 pandas 处理。

也可以用管理员接口查看和下载：

```bash
curl -H "Authorization: Bearer <admin_token>" \
  https://your-domain/api/admin/performance/logs

curl -H "Authorization: Bearer <admin_token>" \
  -o perf-2026-06-07.jsonl \
  https://your-domain/api/admin/performance/logs/perf-2026-06-07.jsonl
```

生成 30 分钟有效的临时公开下载链接：

```bash
curl -X POST \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"ttl_seconds":1800}' \
  https://your-domain/api/admin/performance/logs/perf-2026-06-07.jsonl/share-link
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `PERF_MONITOR_ENABLED` | `true` | 总开关，设为 `0`/`false` 可关闭 |
| `PERF_LOG_DIR` | 空 | 自定义日志目录；默认在 `user_data_simple/_shared/performance` |
| `PERF_SHARE_SECRET` | 空 | 临时下载链接签名密钥；默认使用 Flask `SECRET_KEY` |
| `PERF_SLOW_REQUEST_MS` | `1000` | 后端慢请求阈值，也作为前端慢 API 参考阈值 |
| `PERF_BACKEND_SAMPLE_RATE` | `0.02` | 后端非慢/非错误请求抽样率 |
| `PERF_BROWSER_SAMPLE_RATE` | `1.0` | 浏览器会话抽样率 |
| `PERF_MAX_REPORT_EVENTS` | `60` | 单次浏览器上报最多事件数 |
| `PERF_MAX_REPORT_BYTES` | `262144` | 单次上报最大字节数 |

## 快速查看

慢 API：

```bash
jq -r 'select(.event.type=="api" or .event.type=="http_request") | [.recorded_at,.event.source,.event.method,.event.path // .event.url.path,.event.status,.event.duration_ms] | @tsv' \
  user_data_simple/_shared/performance/perf-YYYY-MM-DD.jsonl
```

浏览器长任务：

```bash
jq -r 'select(.event.type=="long_task") | [.recorded_at,.event.page,.event.section,.event.duration_ms] | @tsv' \
  user_data_simple/_shared/performance/perf-YYYY-MM-DD.jsonl
```
