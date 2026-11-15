#!/usr/bin/env python3
"""WeCom MCP Server — 让 AI 客户端操作你自己的企业微信。

默认以 stdio 方式运行（本地进程，Claude Desktop / Cursor 直接拉起）：

    python server.py

以 HTTP 方式运行（部署在服务器上，给 Operit 等远程客户端用）：

    python server.py --http

两层凭证：
  1) MCP 客户端连本服务：Authorization: Bearer <WECOM_MCP_TOKEN>（仅 HTTP 模式需要）
  2) 本服务调企业微信：WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_SECRET
     并且需要在企业微信后台配置「企业可信 IP」，否则发消息会返回 60020。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request

import requests
from mcp.server.fastmcp import FastMCP

CORP_ID = os.environ.get("WECOM_CORP_ID", "").strip()
AGENT_ID = int((os.environ.get("WECOM_AGENT_ID", "0") or "0").strip())
SECRET = os.environ.get("WECOM_SECRET", "").strip()
MCP_TOKEN = os.environ.get("WECOM_MCP_TOKEN", "").strip()
HOST = os.environ.get("MCP_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("MCP_PORT", "18131").strip())

# 语音合成（仅 send_voice 需要）：MiniMax TTS
MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY", "").strip()
MINIMAX_VOICE = os.environ.get("MINIMAX_VOICE_ID", "").strip()
TTS_URL = "https://api.minimaxi.com/v1/t2a_v2"
TTS_MODEL = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-hd").strip()

API = "https://qyapi.weixin.qq.com/cgi-bin"

# 常见错误码翻译成能直接照做的提示
HINTS = {
    40001: "Secret 无效或已重置，请到应用详情重新获取",
    40013: "CorpID 不对，检查「我的企业 → 企业信息」里的企业 ID",
    40003: "UserID 不存在",
    60020: "当前出口 IP 不在「企业可信 IP」列表里，去应用详情页添加",
    81013: "接收人不在应用可见范围内，去「应用管理 → 可见范围」添加",
    45009: "接口调用超过频率限制，稍后再试",
    60011: "没有通讯录权限，需要在应用里开启通讯录读取",
    301002: "无权限操作该成员",
}

_tok = {"v": "", "exp": 0.0}
_lock = threading.Lock()
mcp = FastMCP("wecom")


def _explain(resp):
    """返回 None 表示成功，否则返回人话错误。"""
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    code = int(data.get("errcode", 0) or 0)
    if code == 0:
        return None
    msg = data.get("errmsg", "")
    hint = HINTS.get(code, "")
    return f"errcode={code} {msg}" + (f"（{hint}）" if hint else "")


def _token(force=False):
    """access_token 有效期 7200 秒，缓存并提前 5 分钟刷新，避免触发频率限制。"""
    with _lock:
        if not force and _tok["v"] and time.time() < _tok["exp"]:
            return _tok["v"]
        resp = requests.get(
            f"{API}/gettoken",
            params={"corpid": CORP_ID, "corpsecret": SECRET},
            timeout=10,
        )
        err = _explain(resp)
        if err:
            raise RuntimeError("获取 access_token 失败：" + err)
        data = resp.json()
        _tok["v"] = data["access_token"]
        _tok["exp"] = time.time() + int(data.get("expires_in", 7200)) - 300
        return _tok["v"]


def _send(payload):
    payload.setdefault("agentid", AGENT_ID)
    payload.setdefault("safe", 0)
    resp = requests.post(
        f"{API}/message/send",
        params={"access_token": _token()},
        json=payload,
        timeout=15,
    )
    err = _explain(resp)
    if err:
        return "发送失败：" + err
    data = resp.json()
    bad = [u for u in (data.get("invaliduser") or "").split("|") if u]
    note = f"，无效接收人：{', '.join(bad)}" if bad else ""
    return f"已发送（msgid={data.get('msgid', '')[:16]}…）{note}"


def _upload(path, media_type):
    if not os.path.isfile(path):
        raise RuntimeError(f"文件不存在：{path}")
    with open(path, "rb") as fh:
        resp = requests.post(
            f"{API}/media/upload",
            params={"access_token": _token(), "type": media_type},
            files={"media": (os.path.basename(path), fh)},
            timeout=90,
        )
    err = _explain(resp)
    if err:
        raise RuntimeError("上传素材失败：" + err)
    return resp.json()["media_id"]


@mcp.tool()
def whoami() -> str:
    """检查配置是否可用。返回企业 ID 后四位、应用 ID，以及 access_token 能否正常获取。排错时先调这个。"""
    tok = _token()
    return f"corp_id=***{CORP_ID[-4:]} agent_id={AGENT_ID} token=ok(len={len(tok)})"


@mcp.tool()
def send_text(to_user: str, content: str) -> str:
    """给企业微信成员发文本消息。to_user 为 UserID，多个用 | 分隔，@all 表示应用可见范围内所有人。"""
    return _send({"touser": to_user, "msgtype": "text", "text": {"content": content}})


@mcp.tool()
def send_image(to_user: str, file_path: str) -> str:
    """发送本地图片。file_path 是本机绝对路径，支持 jpg / png。"""
    media_id = _upload(file_path, "image")
    return _send({"touser": to_user, "msgtype": "image", "image": {"media_id": media_id}})


@mcp.tool()
def send_file(to_user: str, file_path: str) -> str:
    """发送本地文件（文档、压缩包等）。file_path 是本机绝对路径。"""
    media_id = _upload(file_path, "file")
    return _send({"touser": to_user, "msgtype": "file", "file": {"media_id": media_id}})


def _tts_amr(text: str) -> str:
    """文字 → mp3（MiniMax TTS）→ 8kHz AMR-NB（企业微信只认 amr）。返回临时文件路径。"""
    if not MINIMAX_KEY or not MINIMAX_VOICE:
        raise RuntimeError(
            "发语音需要配置 MINIMAX_API_KEY 和 MINIMAX_VOICE_ID；"
            "不配置的话请改用 send_text"
        )
    body = json.dumps({
        "model": TTS_MODEL,
        "text": text[:200],
        "stream": False,
        "voice_setting": {"voice_id": MINIMAX_VOICE, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 24000, "bitrate": 128000,
                          "format": "mp3", "channel": 1},
    }).encode()
    req = urllib.request.Request(
        TTS_URL, data=body,
        headers={"Authorization": "Bearer " + MINIMAX_KEY,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    base = data.get("base_resp") or {}
    if base.get("status_code"):
        raise RuntimeError(f"TTS 失败 {base.get('status_code')} {base.get('status_msg')}")
    audio = (data.get("data") or {}).get("audio") or ""
    if not audio:
        raise RuntimeError("TTS 未返回音频，检查 MINIMAX_VOICE_ID 是否有效")

    work = tempfile.mkdtemp(prefix="wecom-tts-")
    mp3_path = os.path.join(work, "voice.mp3")
    amr_path = os.path.join(work, "voice.amr")
    with open(mp3_path, "wb") as fh:
        fh.write(bytes.fromhex(audio))
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path,
             "-ar", "8000", "-ac", "1", "-c:a", "libopencore_amrnb",
             "-b:a", "12.2k", amr_path],
            check=True, capture_output=True, timeout=90,
        )
    except FileNotFoundError:
        raise RuntimeError("没有找到 ffmpeg，先安装：apt install ffmpeg")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("转 AMR 失败：" + (exc.stderr or b"").decode()[:200])
    return amr_path


@mcp.tool()
def send_voice(to_user: str, text: str) -> str:
    """把一段文字合成为语音并发过去（MiniMax TTS）。text 最长约 200 字，对应 60 秒上限。
    需要配置 MINIMAX_API_KEY 和 MINIMAX_VOICE_ID，服务器还需安装 ffmpeg。"""
    media_id = _upload(_tts_amr(text), "voice")
    return _send({"touser": to_user, "msgtype": "voice", "voice": {"media_id": media_id}})


@mcp.tool()
def list_members(department_id: int = 1, fetch_child: bool = True) -> str:
    """列出通讯录成员。department_id 默认 1（根部门），fetch_child 表示是否递归子部门。需要通讯录读取权限。"""
    resp = requests.get(
        f"{API}/user/list",
        params={
            "access_token": _token(),
            "department_id": department_id,
            "fetch_child": 1 if fetch_child else 0,
        },
        timeout=20,
    )
    err = _explain(resp)
    if err:
        return "查询失败：" + err
    users = resp.json().get("userlist", [])
    lines = [f"{u.get('userid')}  {u.get('name', '')}" for u in users]
    return f"共 {len(users)} 人：\n" + "\n".join(lines[:100])


class BearerGuard:
    """给 ASGI 加一层 Bearer 校验；WECOM_MCP_TOKEN 为空时放行。"""

    def __init__(self, app, token):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self.token:
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"").decode()
            if got != f"Bearer {self.token}":
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized"}',
                })
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    if not (CORP_ID and AGENT_ID and SECRET):
        raise SystemExit("缺少环境变量 WECOM_CORP_ID / WECOM_AGENT_ID / WECOM_SECRET，参考 .env.example")

    if "--http" not in sys.argv:
        # 默认 stdio：由客户端作为子进程拉起，凭证通过 env 传入
        mcp.run()
    else:
        if not MCP_TOKEN:
            print("[warn] 未设置 WECOM_MCP_TOKEN，HTTP 模式将不校验客户端令牌", file=sys.stderr)
        mcp.settings.host = HOST
        mcp.settings.port = PORT
        mcp.settings.streamable_http_path = "/mcp"

        import uvicorn

        app = mcp.streamable_http_app()
        if MCP_TOKEN:
            app = BearerGuard(app, MCP_TOKEN)
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
