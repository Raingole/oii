package dev.yin2hao.smsbridge.root

import android.content.Context
import android.util.Base64
import java.util.concurrent.TimeUnit

object RootManager {
    private const val BASE = "/data/adb/sms-bridge"
    private const val SCRIPT = "/data/adb/service.d/sms-bridge.sh"
    fun isRootAvailable(): Boolean = runCatching { exec("id").first == 0 }.getOrDefault(false)
    fun requestRoot(): Boolean = isRootAvailable()
    fun manager(): String = when {
        exec("test -d /data/adb/ksu").first == 0 -> "KernelSU"
        exec("test -d /data/adb/magisk").first == 0 -> "Magisk"
        exec("test -d /data/adb/ap").first == 0 -> "APatch"
        else -> "Root"
    }
    fun installDaemon(context: Context): Boolean {
        val script = context.assets.open("sms-bridge.sh").use { it.readBytes() }
        val encoded = Base64.encodeToString(script, Base64.NO_WRAP)
        return exec("mkdir -p /data/adb/service.d $BASE/pending $BASE/log && echo $encoded | base64 -d > $SCRIPT && chmod 755 $SCRIPT && chmod 700 $BASE $BASE/pending $BASE/log").first == 0
    }
    fun writeConfig(url: String, token: String, pollInterval: Int = 3): Boolean {
        val text = "CONTROLLER_URL=${url.trim().trimEnd('/')}\nTOKEN=${token.trim()}\nPOLL_INTERVAL=$pollInterval\n"
        val encoded = Base64.encodeToString(text.toByteArray(), Base64.NO_WRAP)
        return exec("mkdir -p $BASE && echo $encoded | base64 -d > $BASE/config && chmod 600 $BASE/config").first == 0
    }
    fun setEnabled(enabled: Boolean): Boolean {
        return exec("mkdir -p $BASE && printf '${if (enabled) "1" else "0"}' > $BASE/enabled && chmod 600 $BASE/enabled").first == 0
    }
    fun startDaemon(): Boolean = exec("sh $SCRIPT >/dev/null 2>&1 &").first == 0
    fun stopDaemon(): Boolean = setEnabled(false)
    fun state(): String = exec("cat $BASE/state 2>/dev/null").second.trim()
    fun heartbeat(): Long = exec("cat $BASE/heartbeat 2>/dev/null").second.trim().toLongOrNull() ?: 0L
    fun lastSmsId(): String = exec("cat $BASE/last_sms_id 2>/dev/null").second.trim()
    fun pid(): String = exec("cat $BASE/pid 2>/dev/null").second.trim()
    fun exec(command: String): Pair<Int, String> {
        val process = ProcessBuilder("su", "-c", command).redirectErrorStream(true).start()
        if (!process.waitFor(8, TimeUnit.SECONDS)) { process.destroyForcibly(); return 124 to "" }
        return process.exitValue() to process.inputStream.bufferedReader().use { it.readText() }
    }
}
