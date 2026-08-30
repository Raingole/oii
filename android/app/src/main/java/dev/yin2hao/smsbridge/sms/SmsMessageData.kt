package dev.yin2hao.smsbridge.sms

data class SmsMessageData(val sourceId: String?, val sender: String, val body: String, val timestamp: Long, val subscriptionId: Int?)
