# ESP32-C3 关门提示音

本服务独立运行。使用 Python 3.10+、Waitress、FFmpeg、systemd 和 Nginx，应用仅监听 127.0.0.1:18022。SQLite 保存音频列表和当前选项，PCM 文件与数据库都保存在 /var/lib/door-audio，重启后继续使用。上传可逐段进行。

## 在 Ubuntu 上部署

如果这台服务器也运行 OII，在仓库根目录执行以下命令，随后按下文配置 Nginx。

```bash
sudo apt-get install python3-venv ffmpeg nginx
sudo install -d -m 0750 /etc/door-audio
sudo test -e /etc/door-audio/door-audio.env || sudo install -m 0600 deploy/door-audio/door-audio.env.example /etc/door-audio/door-audio.env
sudoedit /etc/door-audio/door-audio.env
sudo bash auto-start.sh install
```

填写下文所列的环境变量后，安装入口会安装并启动 `door-audio.service`，并设置开机自启。已有环境文件和音频数据可继续使用。更新关门音频代码后重新运行该安装入口。

如果只部署关门音频，在仓库根目录执行以下命令。首次部署创建专用系统用户。

```bash
sudo apt-get install python3-venv ffmpeg nginx
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin door-audio
sudo install -d -m 0755 /opt/door-audio /opt/door-audio/templates
sudo install -m 0644 deploy/door-audio/{app.py,requirements.txt} /opt/door-audio/
sudo install -m 0644 deploy/door-audio/templates/admin.html /opt/door-audio/templates/
sudo python3 -m venv /opt/door-audio/.venv
sudo /opt/door-audio/.venv/bin/pip install -r /opt/door-audio/requirements.txt
sudo install -d -m 0750 /etc/door-audio
sudo install -m 0600 deploy/door-audio/door-audio.env.example /etc/door-audio/door-audio.env
sudoedit /etc/door-audio/door-audio.env
sudo install -m 0644 deploy/door-audio/door-audio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now door-audio
```

在 /etc/door-audio/door-audio.env 中设置 ADMIN_USERNAME、独立的 ADMIN_PASSWORD（至少 12 字符）和 DEVICE_TOKEN。将 DEVICE_TOKEN 设为固件当前配置的值。此文件仅放在部署机上。以后修改配置后运行 `sudo systemctl restart door-audio`。已有部署升级时保留环境文件和 /var/lib/door-audio。

## HTTPS 和固件 HTTP 入口

Nginx 必须能访问本机的 127.0.0.1:18022。为 36.212.7.43 准备受客户端信任的证书，证书的 SAN 应包含该 IP。若采用域名证书，将配置中的 server_name 和访问地址替换为实际域名。Let’s Encrypt 的 IP 证书需要 shortlived profile 和自动续期，并在续期后 reload Nginx。证书签发方式参见 [Certbot IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/)。

将 nginx.conf 中的证书路径改为实际路径，然后安装配置。若 443 或 8022 已由其他站点使用，先检查现有监听，将相关 location 合并进对应站点。

```bash
sudo install -m 0644 deploy/door-audio/door-audio-device.inc /etc/nginx/
sudo install -m 0644 deploy/door-audio/nginx.conf /etc/nginx/conf.d/door-audio.conf
sudo nginx -t && sudo systemctl reload nginx
```

| 用途 | 地址 |
| --- | --- |
| 中文管理页 | https://36.212.7.43/ |
| HTTPS 设备接口 | https://36.212.7.43/api/device/door-audio |
| 已刷固件的兼容接口 | http://36.212.7.43:8022/api/device/door-audio |

在云安全组和主机防火墙放行 TCP 443 和 8022。HTTP 8022 仅暴露设备接口，设备 Token 经此入口以明文发送。

## 验证

管理页登录后，上传音频并选择“保存选择”。使用 HTTPS 检查固定长度和音频响应头。

```bash
read -rs DEVICE_TOKEN
curl --http1.1 -sS -D headers.txt -o door.pcm \
  -H "Authorization: Bearer $DEVICE_TOKEN" \
  -H 'Accept-Encoding: gzip' \
  https://36.212.7.43/api/device/door-audio
cat headers.txt
wc -c < door.pcm
unset DEVICE_TOKEN
```

有选中音频时应返回 200，包含 Content-Type: application/octet-stream、Content-Length（与文件字节数相同）、X-Audio-Format: pcm_s16le、X-Audio-Sample-Rate: 32000 和 X-Audio-Channels: 1。响应没有 Content-Encoding。选择“不播放”后应返回 204。缺少或错误 Token 应返回 401。
