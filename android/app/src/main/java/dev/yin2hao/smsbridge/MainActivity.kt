package dev.yin2hao.smsbridge

import android.os.Bundle
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import dev.yin2hao.smsbridge.network.ControllerClient
import dev.yin2hao.smsbridge.network.SmsEventRequest
import dev.yin2hao.smsbridge.root.RootManager
import dev.yin2hao.smsbridge.settings.SettingsStore
import kotlinx.coroutines.*
import java.util.UUID

class MainActivity : AppCompatActivity() {
    private lateinit var settings: SettingsStore
    private lateinit var url: EditText
    private lateinit var token: EditText
    private lateinit var status: TextView

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setContentView(R.layout.activity_main)
        settings = SettingsStore(this)
        url = findViewById(R.id.controllerUrl)
        token = findViewById(R.id.token)
        status = findViewById(R.id.status)
        url.setText(settings.controllerUrl)
        token.setText(settings.token)
        findViewById<Spinner>(R.id.mode).visibility = android.view.View.GONE
        findViewById<Button>(R.id.save).setOnClickListener { enableDaemon() }
        findViewById<Button>(R.id.stop).setOnClickListener { disableDaemon() }
        findViewById<Button>(R.id.requestRoot).setOnClickListener { requestRoot() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { testConnection() }
        refreshStatus()
    }

    private fun enableDaemon() {
        settings.controllerUrl = url.text.toString()
        settings.token = token.text.toString()
        if (!RootManager.requestRoot() || !RootManager.installDaemon(this) || !RootManager.writeConfig(settings.controllerUrl, settings.token)) {
            Toast.makeText(this, "Root 配置失败", Toast.LENGTH_LONG).show(); return
        }
        settings.enabled = true
        RootManager.setEnabled(true)
        RootManager.startDaemon()
        Toast.makeText(this, "KernelSU daemon 已启用", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }

    private fun disableDaemon() {
        settings.enabled = false
        RootManager.stopDaemon()
        Toast.makeText(this, "短信监听已停止", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }

    private fun requestRoot() {
        val ok = RootManager.requestRoot()
        if (ok) RootManager.installDaemon(this)
        Toast.makeText(this, if (ok) "Root 已授权：${RootManager.manager()}" else "Root 不可用", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }

    private fun testConnection() {
        val event = SmsEventRequest("test-${UUID.randomUUID()}", System.currentTimeMillis(), "SMS-Bridge-Test", "SMS Bridge test", "123456")
        CoroutineScope(Dispatchers.IO).launch {
            val ok = ControllerClient().send(url.text.toString(), token.text.toString(), event).getOrNull() == true
            withContext(Dispatchers.Main) { Toast.makeText(this@MainActivity, if (ok) "中控连接成功" else "中控连接失败或 Token 错误", Toast.LENGTH_LONG).show() }
        }
    }

    private fun refreshStatus() {
        val heartbeat = RootManager.heartbeat()
        val age = if (heartbeat == 0L) "无" else "${(System.currentTimeMillis() - heartbeat) / 1000} 秒前"
        status.text = "运行方式：KernelSU Root daemon\nRoot：${if (RootManager.isRootAvailable()) "已授权 (${RootManager.manager()})" else "未授权"}\nDaemon：${RootManager.state().ifBlank { "未运行" }}\nPID：${RootManager.pid().ifBlank { "-" }}\n最后心跳：$age\nSMS 游标：${RootManager.lastSmsId().ifBlank { "未建立" }}"
    }
}
