package dev.yin2hao.smsbridge.root

import java.util.concurrent.TimeUnit

object RootManager {
    fun isRootAvailable(): Boolean = runCatching { exec("id").first == 0 }.getOrDefault(false)
    fun requestRoot(): Boolean = isRootAvailable()
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
