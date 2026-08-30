package dev.yin2hao.smsbridge

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import dev.yin2hao.smsbridge.network.ControllerClient
import dev.yin2hao.smsbridge.network.SmsEventRequest
import dev.yin2hao.smsbridge.root.RootManager
import dev.yin2hao.smsbridge.settings.SettingsStore
import dev.yin2hao.smsbridge.sms.SmsMonitorService
import kotlinx.coroutines.*
import java.util.UUID

class MainActivity : AppCompatActivity() {
    private lateinit var settings: SettingsStore; private lateinit var url: EditText; private lateinit var token: EditText; private lateinit var status: TextView
    private val smsPermission = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { refreshStatus() }
    override fun onCreate(state: Bundle?) { super.onCreate(state); setContentView(R.layout.activity_main); settings = SettingsStore(this); url = findViewById(R.id.controllerUrl); token = findViewById(R.id.token); status = findViewById(R.id.status); url.setText(settings.controllerUrl); token.setText(settings.token); findViewById<Spinner>(R.id.mode).adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, listOf("自动", "仅标准 SMS", "标准 SMS + Root fallback")); findViewById<Button>(R.id.save).setOnClickListener { save(true) }; findViewById<Button>(R.id.requestSms).setOnClickListener { smsPermission.launch(arrayOf(Manifest.permission.RECEIVE_SMS, Manifest.permission.READ_SMS)) }; findViewById<Button>(R.id.requestRoot).setOnClickListener { Toast.makeText(this, if (RootManager.requestRoot()) "Root 已授权" else "Root 不可用", Toast.LENGTH_SHORT).show(); refreshStatus() }; findViewById<Button>(R.id.testConnection).setOnClickListener { testConnection() }; refreshStatus() }
    private fun save(enable: Boolean) { settings.controllerUrl = url.text.toString(); settings.token = token.text.toString(); settings.enabled = enable; settings.rootFallback = when (findViewById<Spinner>(R.id.mode).selectedItemPosition) { 0 -> RootManager.isRootAvailable(); 2 -> true; else -> false }; if (enable) SmsMonitorService.start(this); refreshStatus() }
    private fun testConnection() { save(false); val event = SmsEventRequest("test-${UUID.randomUUID()}", System.currentTimeMillis(), "SMS-Bridge-Test", "SMS Bridge 测试事件", "123456"); CoroutineScope(Dispatchers.IO).launch { val ok = ControllerClient().send(settings.controllerUrl, settings.token, event).getOrNull() == true; withContext(Dispatchers.Main) { Toast.makeText(this@MainActivity, if (ok) "中控连接成功" else "中控连接失败或 Token 错误", Toast.LENGTH_LONG).show() } } }
    private fun refreshStatus() { val sms = ContextCompat.checkSelfPermission(this, Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED; status.text = "SMS 权限：${if (sms) "已授权" else "未授权"}\nRoot：${if (RootManager.isRootAvailable()) "已授权/可用" else "未授权或不可用"}\n监听：${if (settings.enabled) "已启用" else "未启用"}" }
}
