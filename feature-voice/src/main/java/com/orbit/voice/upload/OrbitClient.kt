package com.orbit.voice.upload

import com.orbit.voice.talk.Reply
import com.orbit.voice.talk.ReplyParser
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.buffer
import okio.source
import java.io.File
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import javax.net.SocketFactory

/** HTTP calls to the companion (API in docs/voice-notes.md → Upload API). Blocking; call off the main thread. */
class OrbitClient(socketFactory: SocketFactory = SocketFactory.getDefault()) {

    private val http = OkHttpClient.Builder()
        .socketFactory(socketFactory) // bound to the Wi-Fi network, never LTE
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    @Serializable
    private data class Health(val name: String, val api: Int = 1, val proof: String)

    @Serializable
    private data class PutResult(val id: String, val sha256: String)

    /** GET /v1/health with a fresh nonce. `verified` only if [token] is given and the proof matches. */
    fun health(ip: String, port: Int, token: String?, connectTimeoutMs: Long = 3_000): FoundServer? {
        val nonce = ByteArray(16).also { SecureRandom().nextBytes(it) }.toHex()
        val client = http.newBuilder().connectTimeout(connectTimeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(2, TimeUnit.SECONDS).build()
        return runCatching {
            client.newCall(Request.Builder().url("http://$ip:$port/v1/health?nonce=$nonce").build()).execute().use { r ->
                if (!r.isSuccessful) return null
                val h = JSON.decodeFromString(Health.serializer(), r.body.string())
                FoundServer(ip, port, h.name, verified = token != null && constantTimeEquals(h.proof, hmac(token, nonce)))
            }
        }.getOrNull()
    }

    /** HEAD /v1/notes/{id}: true = server already has it. */
    fun has(ip: String, port: Int, token: String, id: String): Boolean =
        http.newCall(
            Request.Builder().url("http://$ip:$port/v1/notes/$id").head().header("Authorization", "Bearer $token").build()
        ).execute().use { r ->
            when (r.code) {
                200 -> true
                404 -> false
                else -> throw HttpError(r.code)
            }
        }

    /** PUT /v1/notes/{id}; returns the server's sha256 for the stored audio. */
    fun put(ip: String, port: Int, token: String, id: String, audio: File, meta: File, onProgress: (Float) -> Unit): String {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("audio", audio.name, ProgressBody(audio, onProgress))
            .addFormDataPart("meta", null, meta.readText().toRequestBody(JSON_TYPE))
            .build()
        val req = Request.Builder().url("http://$ip:$port/v1/notes/$id").put(body)
            .header("Authorization", "Bearer $token").build()
        return http.newCall(req).execute().use { r ->
            if (r.code != 200 && r.code != 201) throw HttpError(r.code)
            JSON.decodeFromString(PutResult.serializer(), r.body.string()).sha256
        }
    }

    /** GET /v1/notes/{id}/reply (docs/talk.md). Throws on network errors only. */
    fun reply(ip: String, port: Int, token: String, id: String): Reply =
        http.newCall(
            Request.Builder().url("http://$ip:$port/v1/notes/$id/reply").header("Authorization", "Bearer $token").build()
        ).execute().use { r -> ReplyParser.parse(r.code, r.body.string(), id) }

    /** Downloads [path] (e.g. `/v1/notes/{id}/reply.wav`) from the companion into [dest]. */
    fun download(ip: String, port: Int, token: String, path: String, dest: File) {
        http.newCall(
            Request.Builder().url("http://$ip:$port$path").header("Authorization", "Bearer $token").build()
        ).execute().use { r ->
            if (!r.isSuccessful) throw HttpError(r.code)
            val tmp = File(dest.path + ".part")
            tmp.outputStream().use { out -> r.body.byteStream().copyTo(out) }
            if (!tmp.renameTo(dest)) error("rename failed: ${dest.name}")
        }
    }

    class HttpError(val code: Int) : Exception("HTTP $code")

    private class ProgressBody(private val file: File, private val onProgress: (Float) -> Unit) : RequestBody() {
        override fun contentType() = "audio/aac".toMediaType()
        override fun contentLength() = file.length()
        override fun writeTo(sink: BufferedSink) {
            val total = file.length().coerceAtLeast(1)
            var sent = 0L
            file.source().buffer().use { src ->
                while (true) {
                    val n = src.read(sink.buffer, 16 * 1024)
                    if (n < 0) break
                    sink.emit()
                    sent += n
                    onProgress(sent.toFloat() / total)
                }
            }
        }
    }

    companion object {
        private val JSON = Json { ignoreUnknownKeys = true }
        private val JSON_TYPE = "application/json".toMediaType()

        fun hmac(token: String, nonce: String): String =
            Mac.getInstance("HmacSHA256").run {
                init(SecretKeySpec(token.toByteArray(), "HmacSHA256"))
                doFinal(nonce.toByteArray()).toHex()
            }

        private fun constantTimeEquals(a: String, b: String) =
            java.security.MessageDigest.isEqual(a.lowercase().toByteArray(), b.lowercase().toByteArray())

        private fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }
    }
}
