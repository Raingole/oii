package dev.yin2hao.smsbridge.sms

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import dev.yin2hao.smsbridge.code.VerificationCodeExtractor
import dev.yin2hao.smsbridge.network.ControllerClient
import dev.yin2hao.smsbridge.network.SmsEventRequest
import dev.yin2hao.smsbridge.queue.PendingEventStore
import dev.yin2hao.smsbridge.settings.SettingsStore
import kotlinx.coroutines.*

class SmsMonitorService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var store: PendingEventStore
    private lateinit var settings: SettingsStore
    private var rootMonitorJob: Job? = null
    @Volatile private var controllerOnline = false

    override fun onCreate() {
        super.onCreate()
        store = PendingEventStore(this)
        settings = SettingsStore(this)
        createChannel()
        startForeground(NOTIFICATION_ID, notification())
        serviceScope.launch { queueLoop() }
        if (settings.rootFallback) startRootMonitorOnce()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.hasExtra("event_id") == true) {
            store.enqueue(SmsEventRequest(intent.getStringExtra("event_id")!!, intent.getLongExtra("timestamp", 0), intent.getStringExtra("sender")!!, intent.getStringExtra("body")!!, intent.getStringExtra("code")!!))
        }
        if (settings.rootFallback) startRootMonitorOnce()
        return START_STICKY
    }

    private fun startRootMonitorOnce() {
        if (rootMonitorJob?.isActive == true) return
        rootMonitorJob = serviceScope.launch { rootLoop() }
    }

    private suspend fun rootLoop() {
        var cursor = settings.rootSmsLastId
        if (cursor == null) {
            val baseline = withContext(Dispatchers.IO) { RootSmsSource.maxId() }
            if (baseline == null) {
                Log.w(TAG, "Root SMS baseline query failed")
                return
            }
            cursor = baseline
            settings.rootSmsLastId = baseline
            Log.i(TAG, "Root SMS baseline established: lastId=$baseline")
        }
        while (currentCoroutineContext().isActive) {
            val result = withContext(Dispatchers.IO) { RootSmsSource.recentAfter(cursor!!, 50) }
            if (!result.commandSucceeded) {
                Log.w(TAG, "Root SMS poll failed")
                delay(POLL_INTERVAL_MS)
                continue
            }
            Log.d(TAG, "Root poll: lastId=$cursor newCount=${result.messages.size}")
            for (message in result.messages) {
                val id = message.providerId ?: continue
                if (id <= cursor!!) continue
                val code = VerificationCodeExtractor.extract(message.body)?.code
                if (code != null) {
                    val event = SmsEventRequest(SmsReceiver.stableId(message), message.timestamp, message.sender, message.body, code)
                    store.enqueue(event)
                    Log.i(TAG, "Root SMS discovered: id=$id sender=${maskSender(message.sender)} verification=true code=${maskCode(code)}")
                } else {
                    Log.d(TAG, "Root SMS discovered: id=$id sender=${maskSender(message.sender)} verification=false")
                }
                cursor = id
                settings.rootSmsLastId = id
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    private suspend fun queueLoop() {
        while (currentCoroutineContext().isActive) {
            val token = settings.token
            if (token.isNotBlank()) {
                val client = ControllerClient()
                store.due().forEach { event ->
                    Log.d(TAG, "Sending SMS event: eventId=${event.eventId.take(12)} code=${maskCode(event.code)}")
                    val result = client.send(settings.controllerUrl, token, event)
                    if (result.getOrNull() == true) {
                        store.success(event.eventId); controllerOnline = true
                        Log.i(TAG, "SMS event delivered")
                    } else {
                        store.fail(event.eventId); controllerOnline = false
                    }
                }
            }
            settings.lastHeartbeat = System.currentTimeMillis()
            updateNotification()
            delay(3000)
        }
    }

    private fun createChannel() = getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL, "SMS Bridge", NotificationManager.IMPORTANCE_LOW))
    private fun notification(): Notification = NotificationCompat.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle("SMS Bridge 正在运行").setContentText(if (controllerOnline) "Root监听正常 · 中控正常" else "Root监听正常 · 中控离线").setOngoing(true).build()
    private fun updateNotification() = getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification())
    override fun onTaskRemoved(rootIntent: Intent?) { super.onTaskRemoved(rootIntent) }
    override fun onDestroy() { rootMonitorJob?.cancel(); serviceScope.cancel(); store.close(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "SMSBridge"
        private const val CHANNEL = "sms_bridge"
        private const val NOTIFICATION_ID = 10
        private const val POLL_INTERVAL_MS = 5000L
        fun maskCode(code: String) = if (code.length <= 4) "****" else "${code.take(2)}****${code.takeLast(2)}"
        private fun maskSender(sender: String) = if (sender.length > 6) sender.take(3) + "****" + sender.takeLast(3) else "***"
        fun start(context: Context) = androidx.core.content.ContextCompat.startForegroundService(context, Intent(context, SmsMonitorService::class.java))
    }
}
