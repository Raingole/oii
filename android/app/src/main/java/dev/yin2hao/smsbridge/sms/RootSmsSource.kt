package dev.yin2hao.smsbridge.sms

import dev.yin2hao.smsbridge.root.RootManager

object RootSmsSource {
    data class QueryResult(val messages: List<SmsMessageData>, val commandSucceeded: Boolean)

    fun maxId(): Long? = query(null, 1).messages.maxOfOrNull { it.providerId ?: 0L }

    fun recentAfter(lastId: Long, limit: Int = 50): QueryResult {
        val filtered = query(lastId, limit)
        if (filtered.commandSucceeded) {
            return filtered.copy(messages = filtered.messages.filter { (it.providerId ?: 0L) > lastId })
        }
        return query(null, limit).let { it.copy(messages = it.messages.filter { m -> (m.providerId ?: 0L) > lastId }) }
    }

    private fun query(after: Long?, limit: Int): QueryResult {
        val where = after?.let { " --where \"_id > $it\"" } ?: ""
        val sort = if (after == null) "DESC" else "ASC"
        val command = "content query --uri content://sms/inbox --projection _id,date,address,body,sub_id$where --sort \"_id $sort\" --limit $limit"
        val (status, output) = RootManager.exec(command)
        if (status != 0) return QueryResult(emptyList(), false)
        val messages = output.lineSequence().mapNotNull { line ->
            val id = field(line, "_id")?.toLongOrNull() ?: return@mapNotNull null
            val date = field(line, "date")?.toLongOrNull() ?: return@mapNotNull null
            val address = line.substringAfter("address=", "").substringBefore(", body=").trim()
            val body = line.substringAfter("body=", "").substringBefore(", sub_id=").trim()
            if (address.isBlank()) return@mapNotNull null
            SmsMessageData("root:$id", address, body, date, field(line, "sub_id")?.toIntOrNull(), id)
        }.sortedBy { it.providerId }.toList()
        return QueryResult(messages, true)
    }

    private fun field(line: String, name: String): String? = Regex("(?:^|, )$name=([^, ]+)").find(line)?.groupValues?.get(1)
}
