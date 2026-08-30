package dev.yin2hao.smsbridge.settings

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

class SettingsStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context, "settings", MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )
    var controllerUrl: String
        get() = prefs.getString("url", DEFAULT_URL) ?: DEFAULT_URL
        set(value) = prefs.edit().putString("url", value.trim().trimEnd('/')).apply()
    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) = prefs.edit().putString("token", value.trim()).apply()
    var enabled: Boolean
        get() = prefs.getBoolean("enabled", false)
        set(value) = prefs.edit().putBoolean("enabled", value).apply()
    var rootFallback: Boolean
        get() = prefs.getBoolean("root_fallback", false)
        set(value) = prefs.edit().putBoolean("root_fallback", value).apply()
    var rootSmsLastId: Long?
        get() = if (prefs.contains("root_sms_last_id")) prefs.getLong("root_sms_last_id", 0L) else null
        set(value) { prefs.edit().apply { if (value == null) remove("root_sms_last_id") else putLong("root_sms_last_id", value) }.apply() }
    var lastHeartbeat: Long
        get() = prefs.getLong("last_heartbeat", 0L)
        set(value) = prefs.edit().putLong("last_heartbeat", value).apply()
    fun setEnabledFromRoot(value: Boolean) {
        prefs.edit().putBoolean("enabled", value).apply()
        dev.yin2hao.smsbridge.root.RootManager.setWatchdogEnabled(value)
    }
    companion object { const val DEFAULT_URL = "http://36.212.7.43:8005" }
}
