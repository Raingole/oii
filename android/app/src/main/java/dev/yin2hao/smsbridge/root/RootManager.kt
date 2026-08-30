package dev.yin2hao.smsbridge.root

import java.util.concurrent.TimeUnit

object RootManager {
    private const val STATE_DIR = "/data/adb/sms-bridge"
    private const val STATE_FILE = "$STATE_DIR/enabled"
    fun isRootAvailable(): Boolean = runCatching { exec("id").first == 0 }.getOrDefault(false)
    fun requestRoot(): Boolean = isRootAvailable()
    fun manager(): String = when {
        exec("test -d /data/adb/magisk").first == 0 -> "Magisk"
        exec("test -d /data/adb/ksu").first == 0 -> "KernelSU"
        exec("test -d /data/adb/ap").first == 0 -> "APatch"
        else -> "Root"
    }
    fun setWatchdogEnabled(enabled: Boolean) {
        exec("mkdir -p $STATE_DIR && printf '${if (enabled) "1" else "0"}' > $STATE_FILE")
    }
    fun installWatchdog(): Boolean {
        if (exec("test -d /data/adb/service.d").first != 0) return false
        val script = listOf(
            "#!/system/bin/sh", "PACKAGE=dev.yin2hao.smsbridge",
            "SERVICE=\"\$PACKAGE/.sms.SmsMonitorService\"", "STATE=/data/adb/sms-bridge/enabled",
            "while [ \"\$(getprop sys.boot_completed)\" != \"1\" ]; do sleep 5; done", "sleep 10",
            "while true; do", "  if [ \"\$(cat \"\$STATE\" 2>/dev/null)\" = \"1\" ]; then",
            "    if ! dumpsys activity services \"\$SERVICE\" 2>/dev/null | grep -q \"\$SERVICE\"; then",
            "      am start-foreground-service -n \"\$SERVICE\" >/dev/null 2>&1", "    fi", "  fi", "  sleep 30", "done"
        ).joinToString("\n") + "\n"
        val encoded = android.util.Base64.encodeToString(script.toByteArray(), android.util.Base64.NO_WRAP)
        return exec("mkdir -p /data/adb/sms-bridge && echo $encoded | base64 -d > /data/adb/service.d/sms-bridge-watchdog.sh && chmod 755 /data/adb/service.d/sms-bridge-watchdog.sh").first == 0
    }
    fun exec(command: String): Pair<Int, String> {
        val process = ProcessBuilder("su", "-c", command).redirectErrorStream(true).start()
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
            process.destroyForcibly()
            return 124 to ""
        }
        val output = process.inputStream.bufferedReader().use { it.readText() }
        return process.exitValue() to output
    }
}
