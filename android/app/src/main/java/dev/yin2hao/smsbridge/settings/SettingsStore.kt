package dev.yin2hao.smsbridge.settings

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys

class SettingsStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        "settings", MasterKeys.AES256_GCM_SPEC, context,
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
    companion object { const val DEFAULT_URL = "http://36.212.7.43:8005" }
}
