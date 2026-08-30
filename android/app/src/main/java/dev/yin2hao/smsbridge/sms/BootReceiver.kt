package dev.yin2hao.smsbridge.sms

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dev.yin2hao.smsbridge.settings.SettingsStore

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && SettingsStore(context).enabled) SmsMonitorService.start(context)
    }
}
