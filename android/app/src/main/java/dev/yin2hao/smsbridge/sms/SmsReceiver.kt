package dev.yin2hao.smsbridge.sms

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import dev.yin2hao.smsbridge.code.VerificationCodeExtractor
import dev.yin2hao.smsbridge.network.SmsEventRequest
import java.security.MessageDigest

class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val message = runCatching { StandardSmsSource.fromIntent(intent) }.getOrNull() ?: return
        val code = VerificationCodeExtractor.extract(message.body)?.code ?: return
        val event = SmsEventRequest(stableId(message), message.timestamp, message.sender, message.body, code)
        val serviceIntent = Intent(context, SmsMonitorService::class.java).apply {
            putExtra("event_id", event.eventId); putExtra("timestamp", event.timestamp); putExtra("sender", event.sender); putExtra("body", event.body); putExtra("code", event.code)
        }
        ContextCompat.startForegroundService(context, serviceIntent)
    }
    companion object {
        fun stableId(message: SmsMessageData): String {
            val raw = "${message.subscriptionId ?: ""}|${message.sender}|${message.timestamp}|${message.body.trim().replace(Regex("\\s+"), " ")}"
            return MessageDigest.getInstance("SHA-256").digest(raw.toByteArray()).joinToString("") { "%02x".format(it) }
        }
    }
}
