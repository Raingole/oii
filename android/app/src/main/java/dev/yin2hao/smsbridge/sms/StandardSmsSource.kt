package dev.yin2hao.smsbridge.sms

import android.content.Intent
import android.provider.Telephony

object StandardSmsSource {
    fun fromIntent(intent: Intent): SmsMessageData? {
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isNullOrEmpty()) return null
        val sender = messages.first().originatingAddress ?: return null
        val body = messages.joinToString("") { it.messageBody ?: "" }
        val timestamp = messages.minOf { it.timestampMillis }
        val subscription = intent.extras?.getInt(Telephony.Sms.Intents.EXTRA_SUBSCRIPTION_INDEX, -1)?.takeIf { it >= 0 }
        return SmsMessageData("broadcast", sender, body, timestamp, subscription)
    }
}
