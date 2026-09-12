# SMS Bridge

独立 Kotlin Android App + KernelSU `service.d` Root shell daemon。长期监听由
`/data/adb/service.d/sms-bridge.sh` 完成，使用只读 `content query` 查询短信；APK
只负责配置、安装/启停 daemon 和查看状态。

## 构建

需要 Android SDK 35、JDK 17 和 Gradle 8.7+：

```bash
cd android
gradle test
gradle assembleDebug
```

APK 输出：`app/build/outputs/apk/debug/app-debug.apk`。

安装：

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

首次打开后填写：

- Controller URL：默认 `http://36.212.7.43:8005`
- Token：填写中控 `data/.config.yaml` 中的 SMS webhook token；App 使用
  Android Keystore 加密保存

点击“保存并启用监听”后，APK 通过 `su` 安装并启动 daemon；不会修改短信数据库、
SELinux、AppOps 或短信应用。

## 行为

- 仅当本地验证码识别器找到明确验证码语义时入队
- 成功 HTTP 2xx 删除 pending；网络错误和 HTTP 5xx 按 5 秒间隔重试
- 401/403 视为 Token 配置错误，不无限重试
- pending 超过 15 分钟丢弃，成功事件保留 24 小时
- `event_id = SHA-256(subscriptionId|sender|timestamp|normalizedBody)`，广播和 Root
  发现同一短信时只发送一次
