package dev.yin2hao.smsbridge.network

data class SmsEventRequest(val eventId: String, val timestamp: Long, val sender: String, val body: String, val code: String)
