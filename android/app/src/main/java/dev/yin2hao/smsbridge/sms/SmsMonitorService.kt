package dev.yin2hao.smsbridge.sms

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dev.yin2hao.smsbridge.R
import dev.yin2hao.smsbridge.code.VerificationCodeExtractor
import dev.yin2hao.smsbridge.network.ControllerClient
import dev.yin2hao.smsbridge.network.SmsEventRequest
import dev.yin2hao.smsbridge.queue.PendingEventStore
import dev.yin2hao.smsbridge.settings.SettingsStore
import kotlinx.coroutines.*

class SmsMonitorService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var store: PendingEventStore
    private lateinit var settings: SettingsStore
    override fun onCreate() {
        super.onCreate(); store = PendingEventStore(this); settings = SettingsStore(this); createChannel(); startForeground(10, notification())
        scope.launch { while (isActive) { processQueue(); if (settings.rootFallback) pollRoot(); delay(3000) } }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.hasExtra("event_id") == true) store.enqueue(SmsEventRequest(intent.getStringExtra("event_id")!!, intent.getLongExtra("timestamp", 0), intent.getStringExtra("sender")!!, intent.getStringExtra("body")!!, intent.getStringExtra("code")!!))
        return START_STICKY
    }
    private suspend fun processQueue() {
        val url = settings.controllerUrl; val token = settings.token
        if (token.isBlank()) return
        val client = ControllerClient()
        store.due().forEach { event ->
            val result = client.send(url, token, event)
            if (result.getOrNull() == true) store.success(event.eventId) else store.fail(event.eventId)
        }
    }
    private fun pollRoot() {
        RootSmsSource.recent(System.currentTimeMillis() - 5 * 60 * 1000L).forEach { message ->
            val code = VerificationCodeExtractor.extract(message.body)?.code ?: return@forEach
            store.enqueue(SmsEventRequest(SmsReceiver.stableId(message), message.timestamp, message.sender, message.body, code))
        }
    }
    private fun createChannel() { getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel("sms_bridge", "SMS Bridge", NotificationManager.IMPORTANCE_LOW)) }
    private fun notification(): Notification = NotificationCompat.Builder(this, "sms_bridge").setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle("SMS Bridge").setContentText("正在监听验证码短信").setOngoing(true).build()
    override fun onDestroy() { scope.cancel(); store.close(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null
    companion object { fun start(context: Context) { androidx.core.content.ContextCompat.startForegroundService(context, Intent(context, SmsMonitorService::class.java)) } }
}
