package dev.yin2hao.smsbridge.code

object VerificationCodeExtractor {
    private val context = Regex("(?i)(验证码|校验码|动态码|认证码|安全码|登录码|确认码|verification\\s+code|verify\\s+code|security\\s+code|one[- ]?time\\s+code|otp|authentication\\s+code|confirmation\\s+code|login\\s+code)")
    private val codeAfter = Regex("(?i)(?:验证码|校验码|动态码|认证码|安全码|登录码|确认码|verification\\s+code|verify\\s+code|security\\s+code|one[- ]?time\\s+code|otp|authentication\\s+code|confirmation\\s+code|login\\s+code)\\s*[:：是为为]?\\s*([A-Z0-9]{4,8})")
    private val digits = Regex("(?<![0-9])([0-9]{4,8})(?![0-9])")
    private val alphaNumeric = Regex("(?i)(?<![A-Z0-9])([A-Z][A-Z0-9]{3,7}|[A-Z0-9]*[A-Z][A-Z0-9]{3,7})(?![A-Z0-9])")
    private val exclusions = Regex("(?i)(订单|余额|金额|客服电话|电话|手机号|http|www\\.|20[0-9]{2}[年/-])")

    fun extract(body: String): VerificationResult? {
        if (!context.containsMatchIn(body) || exclusions.containsMatchIn(body.substringBefore("验证码"))) return null
        codeAfter.find(body)?.let { return VerificationResult(it.groupValues[1], 100) }
        val window = context.find(body)!!.let { body.substring(it.range.last + 1, minOf(body.length, it.range.last + 45)) }
        digits.find(window)?.let { return VerificationResult(it.groupValues[1], 90) }
        alphaNumeric.find(window)?.let { return VerificationResult(it.groupValues[1], 85) }
        return null
    }
}
