# Scholar 访问诊断（2026-09-22）

未读取 VPN 账户、订阅或密钥；未修改系统配置。

| 检查 | 结果 |
| --- | --- |
| HTTP_PROXY / HTTPS_PROXY | http://127.0.0.1:7890 |
| 本地端口 | 7890 监听正常 |
| 经环境代理访问 https://www.google.com/generate_204 | HTTP 204 |
| 经环境代理访问 Scholar，查询 LLMShare | HTTP 429；正文含 CAPTCHA 标记 |
| 不使用环境代理访问 Google | ConnectError |
| 不使用环境代理访问 Scholar | ConnectTimeout |

结论：VPN HTTP 代理已生效，Scholar 对当前出口限制访问；不将该结果解释成零篇论文。停止继续请求 Scholar，未采取验证码绕过或代理轮换。

代码新增 `search --proxy` 与 `PAPERGRAPH_PROXY`，保留标准代理环境变量。无需修改系统 VPN。用户可以在本地确认代理出口正常并恢复 Scholar 访问后，再执行命令。显式代理支持 HTTP/HTTPS，错误中不输出代理密码。
