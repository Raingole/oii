package dev.yin2hao.smsbridge.network

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class ControllerClient {
    private val client = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).writeTimeout(10, TimeUnit.SECONDS).build()
    fun send(url: String, token: String, event: SmsEventRequest): Result<Boolean> = runCatching {
        val json = JSONObject().apply { put("event_id", event.eventId); put("timestamp", event.timestamp); put("sender", event.sender); put("body", event.body); put("code", event.code) }
        val request = Request.Builder().url(url.trimEnd('/') + "/api/events/sms").header("Authorization", "Bearer $token").header("Content-Type", "application/json").post(json.toString().toRequestBody("application/json".toMediaType())).build()
        client.newCall(request).execute().use { response ->
            if (response.code == 401 || response.code == 403) throw AuthException()
            response.isSuccessful
        }
    }
    class AuthException : Exception()
}
