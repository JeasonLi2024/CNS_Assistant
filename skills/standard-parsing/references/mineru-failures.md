# MinerU 常见失败

- 服务地址缺失：需要配置 `MINERU_API_BASE_URL`。
- 服务超时：可检查 PDF 大小、服务负载或 `MINERU_REQUEST_TIMEOUT`。
- 返回非 ZIP：说明 MinerU 服务异常或接口不兼容。
- ZIP 无 Markdown：说明服务解析结果不完整。

