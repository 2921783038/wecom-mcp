# wecom-mcp

让 AI 客户端（Claude Desktop / Cursor / Operit / 任意支持 MCP 的工具）操作**你自己的**企业微信：发消息、发图片、发文件、发语音、查通讯录。

> 本项目不提供任何公共托管服务。所有凭证都是使用者自己的，仓库里没有任何可用的企业微信凭据。

## 和 wecom-voice-bot 的区别

| 项目 | 方向 | 场景 |
| --- | --- | --- |
| [wecom-voice-bot](https://github.com/2921783038/wecom-voice-bot) | 企业微信 → AI | 你在企微里发消息，AI 回你 |
| **wecom-mcp** | AI → 企业微信 | 你在 AI 里说「给张三发条消息」，它去发 |

两者可以同时用，互不冲突。

## 两种运行方式

| 方式 | 适合 | 客户端填什么 | 凭证在哪 |
| --- | --- | --- | --- |
| **stdio（默认，推荐）** | 自己电脑上用 | 无需填地址，客户端拉起进程 | 环境变量里 |
| HTTP | 部署在服务器，手机等远程客户端用 | URL + Bearer token | 服务器上 |

个人使用优先 stdio：不用开端口、不用反代、进程随客户端启停。

## 一、准备四个东西

| 变量 | 在哪拿 |
| --- | --- |
| `WECOM_CORP_ID` | 企微后台 → 我的企业 → 企业信息，页面最底部 |
| `WECOM_AGENT_ID` | 应用管理 → 自建应用 → 点进去，顶部 |
| `WECOM_SECRET` | 同上，点「查看」，会发到你的企微客户端 |
| **企业可信 IP** | 应用详情 → 开发者接口 → 企业可信 IP |

> **可信 IP 是最容易卡住的一步。** 企微只接受白名单 IP 调用发消息接口，不加就返回 `60020`。本地跑填你自己的出口 IP（家用宽带是动态的，变了要回来改）；部署在服务器就填服务器 IP。

## 二、stdio 方式（推荐）

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

export WECOM_CORP_ID="wwxxxxxxxxxxxxxxxx"
export WECOM_AGENT_ID="1000002"
export WECOM_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
python server.py
```

跑通后接入 Claude Desktop，编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "wecom": {
      "command": "python",
      "args": ["/absolute/path/to/wecom-mcp/server.py"],
      "env": {
        "WECOM_CORP_ID": "wwxxxxxxxxxxxxxxxx",
        "WECOM_AGENT_ID": "1000002",
        "WECOM_SECRET": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

重启客户端，工具列表里应出现 6 个工具。先让它调 `whoami` 确认连通。

Cursor 等其他客户端同理，都是「命令 + 参数 + 环境变量」三件套。

## 三、HTTP 方式（部署在服务器）

```bash
cp .env.example .env
# 填入四个凭据，并生成一个客户端令牌：
#   python -c "import secrets;print(secrets.token_hex(16))"
python server.py --http
```

默认监听 `127.0.0.1:18131`，路径 `/mcp`。客户端填：

```
URL:   http://<你的地址>/mcp
Token: <WECOM_MCP_TOKEN>
```

需要从外网访问就挂一层反向代理（Caddy 示例）：

```caddyfile
handle_path /wecom/* {
    reverse_proxy 127.0.0.1:18131 {
        header_up Host 127.0.0.1:18131
        flush_interval -1
    }
}
```

`header_up Host` 不能省：MCP SDK 会校验 Host 头，缺了会返回 421 而不是正常响应。

systemd 单元见 `systemd/wecom-mcp.service`。

## 四、工具列表

| 工具 | 作用 |
| --- | --- |
| `whoami` | 检查凭据是否可用，排错先调它 |
| `send_text` | 发文本，`to_user` 支持 `a\|b` 多人和 `@all` |
| `send_image` | 发本地图片 |
| `send_file` | 发本地文件 |
| `send_voice` | 传文字，服务端用 MiniMax TTS 合成语音再发（≤60 秒，约 200 字） |
| `list_members` | 查通讯录 |

工具数量刻意控制在 6 个。工具太多会让模型选择准确率下降。

`send_voice` 的内部流程是：文字 → MiniMax TTS 合成 mp3 → ffmpeg 转 8 kHz AMR-NB → 上传素材 → 发送。因此需要额外安装 `ffmpeg`，并在 `.env` 里配置 `MINIMAX_API_KEY` 和 `MINIMAX_VOICE_ID`。不配这两项时其余 5 个工具照常工作，只会在调用 `send_voice` 时返回明确提示。

> 为什么不用「传 amr 文件路径」的方式：MCP 客户端可能在另一台机器上（比如手机），无法提供服务器本地路径。传文字才是各端都能用的接口。

> 说明：企业微信虽然有 `markdown` 消息类型，但部分客户端和「微信插件」接收端会提示「暂时不支持此类消息」，因此本项目没有提供。需要排版时用 `send_text` 纯文本即可。

## 五、常见错误

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| `40001` | Secret 无效 | 去应用详情重新获取 |
| `40013` | CorpID 不对 | 核对「我的企业 → 企业信息」 |
| `60020` | IP 不在可信列表 | 把当前出口 IP 加进「企业可信 IP」 |
| `81013` | 接收人不在可见范围 | 应用管理 → 可见范围 里添加 |
| `60011` | 无通讯录权限 | 开启应用的通讯录读取权限 |
| `45009` | 调用超频 | access_token 已做缓存，若仍出现说明并发过高 |

## 六、安全提醒

- **不要把 HTTP 地址和 token 发到公开群或截图里**。拿到 token 的人可以用你的企业微信身份给任何人发消息。
- `WECOM_MCP_TOKEN` 建议定期更换；更换后重启服务。
- 只给自己或可信同事使用；企业微信自建应用用于内部通知，不要拿来做群发营销。
- 仓库里永远不要提交 `.env`，`.gitignore` 已经挡掉。

## License

MIT
