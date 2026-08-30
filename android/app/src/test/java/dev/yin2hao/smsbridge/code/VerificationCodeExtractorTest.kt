package dev.yin2hao.smsbridge.code

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VerificationCodeExtractorTest {
    @Test fun commonCodes() {
        assertEquals("382915", VerificationCodeExtractor.extract("【Microsoft】验证码 382915，5分钟内有效")?.code)
        assertEquals("1234", VerificationCodeExtractor.extract("验证码：1234")?.code)
        assertEquals("728194", VerificationCodeExtractor.extract("Your verification code is 728194")?.code)
        assertEquals("A7K92F", VerificationCodeExtractor.extract("Security code: A7K92F")?.code)
        assertEquals("382915", VerificationCodeExtractor.extract("验证码382915，订单号92838171")?.code)
    }

    @Test fun nonVerificationNumbersAreIgnored() {
        assertNull(VerificationCodeExtractor.extract("您的订单号为 382915"))
        assertNull(VerificationCodeExtractor.extract("客服电话 1008611"))
        assertNull(VerificationCodeExtractor.extract("余额 382915 元"))
    }
}
