package dev.yin2hao.smsbridge.sms

import dev.yin2hao.smsbridge.root.RootManager

object RootSmsSource {
    fun recent(after: Long): List<SmsMessageData> {
        val command = "content query --uri content://sms/inbox --projection _id:date:address:body --where \"date > $after\""
        val (status, output) = RootManager.exec(command)
        if (status != 0) return emptyList()
        return output.lineSequence().mapNotNull { line ->
            val id = Regex("_id=([^, ]+)").find(line)?.groupValues?.get(1) ?: return@mapNotNull null
            val date = Regex("date=([^, ]+)").find(line)?.groupValues?.get(1)?.toLongOrNull() ?: return@mapNotNull null
            val address = Regex("address=(.*?), body=").find(line)?.groupValues?.get(1) ?: return@mapNotNull null
            val body = line.substringAfter("body=", "").trim()
            SmsMessageData("root:$id", address, body, date, null)
        }.toList()
    }
}
